#!/usr/bin/env python3
"""
Database Migration: Add The Camel's transaction ledger

The weekly P&L only gives totals per account. QuickBooks also emails the
transactions behind them ("Camel Weekly Transaction Report", "Camel YTD
Transactions"), which Step 9 used to record as 'skipped' because camelManagement
was meant to import them - but camelManagement has been offline since August
2026, so nothing kept them. This migration adds somewhere to keep every line:

- camel_pl_reports gains status 'ledger' (a Transaction Detail report whose
  lines are in camel_transactions) and generated_at (when QuickBooks ran the
  report, from its footer).
- camel_transactions: one row per transaction line of a 'ledger' report.
- camel_ledger (view): the current ledger. A report restates every transaction
  dated in its period, so each line comes from the newest report covering its
  date; edits and deletions in QuickBooks replace what older reports said.
  Hillel's roll-forward detail lists P&L accounts only, so a report with no
  balance-sheet lines (e.g. prepaid expenses) does not override those.
- camel_pledges: donations logged in camelManagement (pledged, awaiting
  Hillel's confirmation, or confirmed), see finance/import_camel_management.py.

It also forgets the transaction reports Step 9 previously skipped, so the next
run fetches them again and imports their lines.

Safe to re-run: every statement is IF NOT EXISTS / OR REPLACE, and constraints
are dropped and recreated.

No amounts are seeded here - this repo is public. Backfill after running it:

    python3 finance/track_balance.py --import-file "<Hillel roll-forward workbook>.xlsx"
    python3 finance/track_balance.py                      # re-imports the skipped reports
    python3 finance/import_camel_management.py            # FY25 history + pledges
"""

import psycopg2
import os
from dotenv import load_dotenv


def connect_to_db():
    """Connect to Railway PostgreSQL database."""
    load_dotenv()

    conn = psycopg2.connect(
        host=os.getenv('PGHOST'),
        port=os.getenv('PGPORT'),
        database=os.getenv('PGDATABASE'),
        user=os.getenv('PGUSER'),
        password=os.getenv('PGPASSWORD')
    )

    return conn


def run_migration():
    """Add camel_transactions, camel_ledger and camel_pledges; allow 'ledger' reports."""
    conn = connect_to_db()
    cur = conn.cursor()

    try:
        print("Starting migration: Adding The Camel's transaction ledger...")

        print("  - Allowing 'ledger' reports in camel_pl_reports...")
        cur.execute("""
            ALTER TABLE camel_pl_reports
            ADD COLUMN IF NOT EXISTS generated_at TIMESTAMPTZ;
        """)
        cur.execute("""
            SELECT conname FROM pg_constraint
            WHERE conrelid = 'camel_pl_reports'::regclass AND contype = 'c';
        """)
        for (name,) in cur.fetchall():
            cur.execute(f'ALTER TABLE camel_pl_reports DROP CONSTRAINT "{name}";')
        cur.execute("""
            ALTER TABLE camel_pl_reports
            ADD CONSTRAINT camel_pl_reports_status_check
                CHECK (status IN ('imported', 'skipped', 'ledger')),
            ADD CONSTRAINT camel_pl_reports_imported_check
                CHECK (status <> 'imported' OR (as_of_date IS NOT NULL
                                                AND fiscal_year IS NOT NULL
                                                AND net_income IS NOT NULL)),
            ADD CONSTRAINT camel_pl_reports_ledger_check
                CHECK (status <> 'ledger' OR (period_start IS NOT NULL
                                              AND as_of_date IS NOT NULL));
        """)

        print("  - Creating camel_transactions...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS camel_transactions (
                id SERIAL PRIMARY KEY,
                report_id INTEGER NOT NULL REFERENCES camel_pl_reports(id) ON DELETE CASCADE,
                line_no INTEGER NOT NULL,
                txn_date DATE NOT NULL,
                account TEXT,
                section VARCHAR(10) NOT NULL,
                txn_type TEXT,
                num TEXT,
                name TEXT,
                description TEXT,
                amount NUMERIC(12,2) NOT NULL,
                ap_paid TEXT,
                category TEXT,
                UNIQUE (report_id, line_no),
                CHECK (section IN ('income', 'expense', 'balance'))
            );
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_camel_transactions_date
            ON camel_transactions (txn_date);
        """)

        print("  - Creating camel_ledger view...")
        cur.execute("""
            CREATE OR REPLACE VIEW camel_ledger AS
            SELECT
                t.txn_date,
                CASE WHEN EXTRACT(MONTH FROM t.txn_date) >= 7
                     THEN EXTRACT(YEAR FROM t.txn_date)::int + 1
                     ELSE EXTRACT(YEAR FROM t.txn_date)::int END AS fiscal_year,
                t.section,
                t.account,
                t.category,
                t.txn_type,
                t.num,
                t.name,
                t.description,
                t.amount,
                t.ap_paid,
                t.id,
                r.source_id
            FROM camel_transactions t
            JOIN camel_pl_reports r ON r.id = t.report_id
            WHERE t.report_id = (
                SELECT newest.id FROM camel_pl_reports newest
                WHERE newest.status = 'ledger'
                  AND t.txn_date BETWEEN newest.period_start AND newest.as_of_date
                  AND (t.section <> 'balance' OR EXISTS (
                      SELECT 1 FROM camel_transactions b
                      WHERE b.report_id = newest.id AND b.section = 'balance'))
                ORDER BY newest.generated_at DESC NULLS LAST,
                         newest.received_at DESC NULLS LAST, newest.id DESC
                LIMIT 1
            );
        """)

        print("  - Creating camel_pledges...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS camel_pledges (
                id SERIAL PRIMARY KEY,
                mgmt_id TEXT UNIQUE NOT NULL,
                amount NUMERIC(12,2) NOT NULL,
                pledged_at DATE NOT NULL,
                expected_at DATE,
                status VARCHAR(20) NOT NULL,
                confirmed_at TIMESTAMPTZ,
                campaign TEXT,
                camelhack_edition SMALLINT,
                donors TEXT,
                notes TEXT,
                created_at TIMESTAMPTZ,
                migrated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)

        print("  - Forgetting skipped transaction reports so the next run imports them...")
        cur.execute("""
            DELETE FROM camel_pl_reports
            WHERE status = 'skipped' AND filename ILIKE 'Transaction Detail%';
        """)
        print(f"    {cur.rowcount} report(s) will be fetched again")

        conn.commit()
        print("✓ Migration completed successfully!")

        # Verify
        cur.execute("""
            SELECT to_regclass('public.camel_transactions'),
                   to_regclass('public.camel_ledger'),
                   to_regclass('public.camel_pledges'),
                   (SELECT count(*) FROM information_schema.columns
                    WHERE table_name = 'camel_pl_reports' AND column_name = 'generated_at');
        """)
        transactions, ledger, pledges, generated_at = cur.fetchone()
        print(f"\nVerification: {transactions}, {ledger}, {pledges}, "
              f"generated_at column {'present' if generated_at else 'MISSING'}")

    except psycopg2.Error as e:
        print(f"✗ Migration failed: {e}")
        conn.rollback()
        raise

    finally:
        cur.close()
        conn.close()


if __name__ == '__main__':
    run_migration()

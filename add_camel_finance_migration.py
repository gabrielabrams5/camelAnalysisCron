#!/usr/bin/env python3
"""
Database Migration: Add tables for tracking The Camel's balance

Hillel's accountant sent a one-off balance ("roll forward") report as of
2026-10-18 and will not send more, so the cron now tracks the balance itself
from the weekly QuickBooks P&L email (finance/track_balance.py, Step 9).

This migration adds:
- camel_fiscal_years: the opening balance of each fiscal year (FY27 = July 2026
  - June 2027, stored as 2027) and where the figure came from.
- camel_pl_reports: one row per QuickBooks email or workbook seen. P&Ls are
  stored with their fiscal-year-to-date totals and per-account figures; other
  QuickBooks attachments are recorded as 'skipped' so they are not re-fetched.
- camel_balance_history (view): the balance after every imported report
  = opening balance of its fiscal year + net income so far that year.

No amounts are seeded here - this repo is public. After running it, record the
FY27 opening ("Beginning Balance") from Hillel's roll-forward report:

    python3 finance/track_balance.py --set-opening FY27 <amount> \
        --source "Hillel roll-forward report as of 2026-10-18"
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
    """Create camel_fiscal_years, camel_pl_reports and camel_balance_history."""
    conn = connect_to_db()
    cur = conn.cursor()

    try:
        print("Starting migration: Adding Camel balance tracking tables...")

        print("  - Creating camel_fiscal_years...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS camel_fiscal_years (
                fiscal_year SMALLINT PRIMARY KEY,
                opening_balance NUMERIC(12,2) NOT NULL,
                source TEXT NOT NULL,
                recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)

        print("  - Creating camel_pl_reports...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS camel_pl_reports (
                id SERIAL PRIMARY KEY,
                source_id VARCHAR(100) UNIQUE NOT NULL,
                status VARCHAR(10) NOT NULL,
                subject TEXT,
                received_at TIMESTAMPTZ,
                filename TEXT,
                period_start DATE,
                as_of_date DATE,
                fiscal_year SMALLINT,
                total_income NUMERIC(12,2),
                total_expenses NUMERIC(12,2),
                net_income NUMERIC(12,2),
                accounts JSONB,
                imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CHECK (status IN ('imported', 'skipped')),
                CHECK (status = 'skipped' OR (as_of_date IS NOT NULL
                                              AND fiscal_year IS NOT NULL
                                              AND net_income IS NOT NULL))
            );
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_camel_pl_reports_as_of
            ON camel_pl_reports (fiscal_year, as_of_date);
        """)

        print("  - Creating camel_balance_history view...")
        cur.execute("""
            CREATE OR REPLACE VIEW camel_balance_history AS
            SELECT
                r.as_of_date,
                r.fiscal_year,
                f.opening_balance,
                r.total_income,
                r.total_expenses,
                r.net_income,
                f.opening_balance + r.net_income AS balance,
                r.source_id
            FROM camel_pl_reports r
            JOIN camel_fiscal_years f ON f.fiscal_year = r.fiscal_year
            WHERE r.status = 'imported';
        """)

        conn.commit()
        print("✓ Migration completed successfully!")

        # Verify
        cur.execute("""
            SELECT to_regclass('public.camel_fiscal_years'),
                   to_regclass('public.camel_pl_reports'),
                   to_regclass('public.camel_balance_history');
        """)
        print(f"\nVerification: {', '.join(str(t) for t in cur.fetchone())}")

    except psycopg2.Error as e:
        print(f"✗ Migration failed: {e}")
        conn.rollback()
        raise

    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    run_migration()

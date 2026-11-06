#!/usr/bin/env python3
"""
Copy the financial records only camelManagement has into this database.

camelManagement (the board's Next.js app, offline on Railway since August 2026)
kept an expense ledger imported from the "Camel expenses" spreadsheet - Hillel's
QuickBooks export, December 2024 to July 2026 - plus anything the board filed
in the app, and the donations the board logged. Since then Hillel's own
Transaction Detail (the roll-forward workbook, and the weekly QuickBooks
reports) covers everything from July 2025 on, income included, so:

- Expenses dated before the first day Hillel's detail covers become one
  'ledger' report, source_id 'camelManagement:expenses', with camelManagement's
  category on each line (its "CamelHack" category included). Its generated_at
  is left NULL, so any QuickBooks report ever imported for those dates wins.
- Expenses dated after that are already in Hillel's books. They are not copied;
  any without a same-amount line in camel_ledger within a week of its date is
  listed for a human to check - most likely something filed in the app that
  Hillel never paid.
- Donations become camel_pledges rows (pledged, unconfirmed, confirmed or
  not_received), keyed by camelManagement's id.
- Bill payments are not copied: each settles a bill that is already a line,
  and QuickBooks' Transaction Detail leaves them out too.

Safe to re-run: the expense report is replaced and pledges are upserted.

Usage:
    MGMT_DATABASE_URL=postgresql://... python3 finance/import_camel_management.py [--dry-run]

Needs the PG* variables for this database, and the camelManagement database
URL in MGMT_DATABASE_URL (or --mgmt-url). Run add_camel_transactions_migration.py
and import Hillel's transaction detail first: that is what decides the cutoff.
"""

import argparse
import logging
import os
import sys
from datetime import timedelta
from decimal import Decimal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

SOURCE_ID = 'camelManagement:expenses'
CENT = Decimal('0.01')


def without_prisma_params(url: str) -> str:
    """Prisma URLs carry ?schema=public, which libpq rejects."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != 'schema']
    return urlunsplit(parts._replace(query=urlencode(query)))


def fetch_mgmt(mgmt):
    cur = mgmt.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT e.id, e.date::date AS txn_date, e.description, e.memo, e."amountCents" AS cents,
               e."sourceRef" AS source_ref, e."sourceType" AS source_type, e.status::text AS status,
               c.name AS category, v.name AS vendor, p."fullName" AS paid_by
        FROM "Expense" e
        LEFT JOIN "Category" c ON c.id = e."categoryId"
        LEFT JOIN "Vendor" v ON v.id = e."vendorId"
        LEFT JOIN "Person" p ON p.id = e."paidByPersonId"
        ORDER BY e.date, e.id
    """)
    expenses = cur.fetchall()
    cur.execute("""
        SELECT d.id, d."amountCents" AS cents, d."pledgedAt"::date AS pledged_at,
               d."expectedAt"::date AS expected_at, d.status::text AS status,
               d."confirmedAt" AS confirmed_at, d.campaign, d."camelHackEdition" AS edition,
               d.notes, d."createdAt" AS created_at,
               string_agg(p."fullName", ', ' ORDER BY dd."isPrimary" DESC, p."fullName") AS donors
        FROM "Donation" d
        LEFT JOIN "DonationDonor" dd ON dd."donationId" = d.id
        LEFT JOIN "Person" p ON p.id = dd."personId"
        GROUP BY d.id
        ORDER BY d."pledgedAt"
    """)
    donations = cur.fetchall()
    cur.execute('SELECT count(*) AS n FROM "Payment"')
    payments = cur.fetchone()['n']
    return expenses, donations, payments


def describe(e) -> str:
    """The description, with the memo when it says something the description does not."""
    description = (e['description'] or '').strip()
    memo = (e['memo'] or '').strip()
    if memo and memo not in description:
        return f"{description} | {memo}" if description else memo
    return description or None


def import_expenses(cur, expenses):
    cur.execute("""
        SELECT min(period_start) FROM camel_pl_reports
        WHERE status = 'ledger' AND source_id <> %s
    """, (SOURCE_ID,))
    cutoff = cur.fetchone()[0]
    if cutoff is None:
        raise SystemExit("No Hillel transaction detail imported yet - run "
                         "finance/track_balance.py --import-file <roll-forward workbook> first")

    before = [e for e in expenses if e['txn_date'] < cutoff]
    after = [e for e in expenses if e['txn_date'] >= cutoff]

    cur.execute("DELETE FROM camel_pl_reports WHERE source_id = %s", (SOURCE_ID,))
    if before:
        cur.execute("""
            INSERT INTO camel_pl_reports (source_id, status, subject, filename, period_start, as_of_date)
            VALUES (%s, 'ledger', %s, %s, %s, %s)
            RETURNING id
        """, (SOURCE_ID, "camelManagement expense ledger, before Hillel's transaction detail",
              'Camel expenses .xlsx (via camelManagement)', before[0]['txn_date'],
              cutoff - timedelta(days=1)))
        report_id = cur.fetchone()[0]
        psycopg2.extras.execute_values(cur, """
            INSERT INTO camel_transactions
                (report_id, line_no, txn_date, account, section, txn_type, num, name, description,
                 amount, ap_paid, category)
            VALUES %s
        """, [(report_id, i, e['txn_date'], None, 'expense', e['source_type'] or 'Expense',
               e['source_ref'], e['vendor'] or e['paid_by'], describe(e),
               (Decimal(e['cents']) / 100).quantize(CENT), None, e['category'])
              for i, e in enumerate(before, 1)])
    logging.info(f"Expenses: {len(before)} dated before {cutoff} copied into the ledger; "
                 f"{len(after)} dated from {cutoff} on are already in Hillel's books")

    # Matched by one line of the same amount, or by the lines carrying its
    # reference: Hillel splits a bill across accounts (venue hire, food, ...).
    unmatched = []
    for e in after:
        amount = (Decimal(e['cents']) / 100).quantize(CENT)
        week = (e['txn_date'] - timedelta(days=7), e['txn_date'] + timedelta(days=7))
        cur.execute("""
            SELECT bool_or(amount = %s), sum(amount) FILTER (WHERE num = %s)
            FROM camel_ledger
            WHERE txn_date BETWEEN %s AND %s AND section = 'expense'
        """, (amount, e['source_ref'], *week))
        same_amount, by_reference = cur.fetchone()
        if not same_amount and by_reference != amount:
            unmatched.append(e)
    if unmatched:
        logging.warning(f"{len(unmatched)} camelManagement expense(s) have no same-amount line in "
                        f"Hillel's books within a week of their date - check these by hand:")
        for e in unmatched:
            logging.warning(f"    {e['txn_date']}  {Decimal(e['cents']) / 100:>12,.2f}  {e['status']:<18} "
                            f"{(e['category'] or '-'):<20} {describe(e) or ''}"[:200])


def import_pledges(cur, donations):
    for d in donations:
        cur.execute("""
            INSERT INTO camel_pledges
                (mgmt_id, amount, pledged_at, expected_at, status, confirmed_at, campaign,
                 camelhack_edition, donors, notes, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (mgmt_id) DO UPDATE SET
                amount = EXCLUDED.amount, pledged_at = EXCLUDED.pledged_at,
                expected_at = EXCLUDED.expected_at, status = EXCLUDED.status,
                confirmed_at = EXCLUDED.confirmed_at, campaign = EXCLUDED.campaign,
                camelhack_edition = EXCLUDED.camelhack_edition, donors = EXCLUDED.donors,
                notes = EXCLUDED.notes, created_at = EXCLUDED.created_at, migrated_at = NOW()
        """, (d['id'], (Decimal(d['cents']) / 100).quantize(CENT), d['pledged_at'], d['expected_at'],
              d['status'], d['confirmed_at'], d['campaign'],
              int(d['edition']) if d['edition'] and str(d['edition']).isdigit() else None,
              d['donors'], d['notes'], d['created_at']))
    logging.info(f"Pledges: {len(donations)} donation(s) copied into camel_pledges")


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy camelManagement's financial records into this database")
    parser.add_argument('--mgmt-url', help='camelManagement Postgres URL (default: $MGMT_DATABASE_URL)')
    parser.add_argument('--dry-run', action='store_true', help='Do everything but roll back the writes')
    args = parser.parse_args()

    load_dotenv()
    mgmt_url = args.mgmt_url or os.getenv('MGMT_DATABASE_URL')
    if not mgmt_url:
        parser.error('set MGMT_DATABASE_URL or pass --mgmt-url')

    mgmt = psycopg2.connect(without_prisma_params(mgmt_url))
    try:
        expenses, donations, payments = fetch_mgmt(mgmt)
    finally:
        mgmt.close()
    logging.info(f"camelManagement: {len(expenses)} expenses, {len(donations)} donations, "
                 f"{payments} bill payments (not copied)")

    conn = psycopg2.connect(host=os.getenv('PGHOST'), port=os.getenv('PGPORT'),
                            database=os.getenv('PGDATABASE'), user=os.getenv('PGUSER'),
                            password=os.getenv('PGPASSWORD'))
    try:
        cur = conn.cursor()
        import_expenses(cur, expenses)
        import_pledges(cur, donations)
        if args.dry_run:
            conn.rollback()
            logging.info("Dry run - all database writes rolled back")
        else:
            conn.commit()
    finally:
        conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())

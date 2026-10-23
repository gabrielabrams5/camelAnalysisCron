#!/usr/bin/env python3
"""
Track The Camel's balance and expenses from the weekly QuickBooks P&L email.

The Camel's money sits in Hillel's books. Hillel's accountant sent a one-off
balance ("roll forward") report as of 2026-10-18 and will not send more, so we
track the balance ourselves. Every Sunday QuickBooks emails "Camel P&L by Month
updated weekly": a Statement of Activity for the fiscal year to date (July 1 -
that Sunday). So after any report:

    balance = opening balance of the fiscal year + net income so far this FY

which reproduces the accountant's figure for 10/18 exactly. Because each report
restates the whole year, an expense booked late into an earlier month is picked
up by the next report automatically.

Each run (Step 9 of run_luma_pipeline.sh):
1. Searches Gmail - Trash included, since camelManagement bins the QuickBooks
   transaction report it imports - for QuickBooks reports not seen before.
   Each P&L's year-to-date totals and per-account figures go into
   camel_pl_reports. Other QuickBooks attachments (the weekly transaction
   report) are recorded as 'skipped' so they are not fetched again. A report
   that fails to import is not recorded, so the next run retries it.
2. Logs the current balance and what changed since the previous report.
3. Exits 1 if the newest report is more than STALE_AFTER_DAYS old: a stopped
   QuickBooks schedule otherwise looks exactly like a quiet week.

Opening balances live in camel_fiscal_years, never in this repo (it is public).
When a new fiscal year starts without one recorded, the previous year's
opening + its last reported net income is carried forward and flagged in the
log; confirm it with Hillel and correct it with --set-opening.

Usage:
    python3 finance/track_balance.py                    # import new emails, log balance
    python3 finance/track_balance.py --dry-run          # same, roll back all writes
    python3 finance/track_balance.py --no-gmail         # log balance from stored reports
    python3 finance/track_balance.py --import-file X.xlsx   # import a saved P&L workbook
    python3 finance/track_balance.py --set-opening FY27 <amount> --source "where it came from"

Needs the PG* variables, and GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET /
GMAIL_REFRESH_TOKEN for the Gmail search (see finance/gmail_auth.py).
Tables are created by add_camel_finance_migration.py.
"""

import argparse
import hashlib
import json
import logging
import os
import sys
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from gmail_client import GmailAuthError, GmailClient, header
from pl_report import fy_label, parse_pl_workbook

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# QuickBooks mail about The Camel with a spreadsheet attached. 120 days is
# enough for a first run to backfill the current fiscal year's reports.
DEFAULT_GMAIL_QUERY = 'from:quickbooks@notification.intuit.com subject:Camel has:attachment newer_than:120d'

# Reports arrive every Sunday; this allows one to be a couple of days late.
STALE_AFTER_DAYS = 9


class SetupError(RuntimeError):
    pass


def get_db_connection():
    try:
        return psycopg2.connect(
            host=os.getenv('PGHOST'),
            port=os.getenv('PGPORT'),
            database=os.getenv('PGDATABASE'),
            user=os.getenv('PGUSER'),
            password=os.getenv('PGPASSWORD')
        )
    except psycopg2.Error as e:
        raise ConnectionError(f"Failed to connect to database: {e}")


def check_tables(cur):
    cur.execute("SELECT to_regclass('public.camel_pl_reports'), to_regclass('public.camel_fiscal_years')")
    if None in cur.fetchone():
        raise SetupError("camel_pl_reports / camel_fiscal_years do not exist - "
                         "run add_camel_finance_migration.py first")


def money(x: Decimal) -> str:
    return f"{'-' if x < 0 else ''}${abs(x):,.2f}"


def signed(x: Decimal) -> str:
    return f"{'-' if x < 0 else '+'}${abs(x):,.2f}"


def day(d: date) -> str:
    return f"{d:%a %b} {d.day}, {d.year}"


def parse_fiscal_year(text: str) -> int:
    """'FY27', '27' or '2027' -> 2027."""
    value = int(text.strip().upper().removeprefix('FY'))
    return value + 2000 if value < 100 else value


# ---------------------------------------------------------------------------
# Importing reports
# ---------------------------------------------------------------------------

def store_report(cur, source_id, subject, received_at, filename, report):
    cur.execute("""
        INSERT INTO camel_pl_reports
            (source_id, status, subject, received_at, filename, period_start, as_of_date,
             fiscal_year, total_income, total_expenses, net_income, accounts)
        VALUES (%s, 'imported', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (source_id, subject, received_at, filename, report.period_start, report.as_of,
          report.fiscal_year, report.total_income, report.total_expenses, report.net_income,
          json.dumps(report.accounts)))


def store_skipped(cur, source_id, subject, received_at, filenames):
    cur.execute("""
        INSERT INTO camel_pl_reports (source_id, status, subject, received_at, filename)
        VALUES (%s, 'skipped', %s, %s, %s)
    """, (source_id, subject, received_at, filenames))


def import_from_gmail(conn, gmail: GmailClient, query: str, dry_run: bool) -> int:
    """Import every QuickBooks report email not seen before. Returns the number that failed."""
    cur = conn.cursor()
    ids = gmail.search(query)
    cur.execute("SELECT source_id FROM camel_pl_reports WHERE source_id = ANY(%s)", (ids,))
    seen = {row[0] for row in cur.fetchall()}
    new_ids = [i for i in reversed(ids) if i not in seen]  # oldest first
    logging.info(f"Gmail: {len(ids)} QuickBooks email(s) match, {len(new_ids)} not seen before")

    failures = 0
    for message_id in new_ids:
        cur.execute("SAVEPOINT report")
        try:
            message = gmail.get_message(message_id)
            subject = header(message, 'Subject') or ''
            received_at = datetime.fromtimestamp(int(message['internalDate']) / 1000, tz=timezone.utc)

            report, filename, names = None, None, []
            for name, content in gmail.attachments(message):
                names.append(name)
                if name.lower().endswith(('.xlsx', '.xlsm')):
                    report = parse_pl_workbook(content)
                    if report:
                        filename = name
                        break

            if report:
                store_report(cur, message_id, subject, received_at, filename, report)
                logging.info(f"  Imported {fy_label(report.fiscal_year)} P&L as of {report.as_of} "
                             f"({subject!r}, received {received_at:%Y-%m-%d})")
            else:
                store_skipped(cur, message_id, subject, received_at, ', '.join(names) or None)
                logging.info(f"  Skipped {subject!r} (received {received_at:%Y-%m-%d}): no P&L attached")
            cur.execute("RELEASE SAVEPOINT report")
            if not dry_run:
                conn.commit()
        except GmailAuthError:
            raise
        except Exception as e:
            cur.execute("ROLLBACK TO SAVEPOINT report")
            failures += 1
            logging.error(f"  Could not import Gmail message {message_id}: {e} (will retry next run)")
    return failures


def import_file(conn, path: str) -> int:
    content = Path(path).read_bytes()
    source_id = 'file:' + hashlib.sha256(content).hexdigest()[:40]
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM camel_pl_reports WHERE source_id = %s", (source_id,))
    if cur.fetchone():
        logging.info(f"{path} was already imported")
        return 0
    report = parse_pl_workbook(content)
    if report is None:
        logging.error(f"{path} has no Statement of Activity sheet")
        return 1
    store_report(cur, source_id, f"manual import of {Path(path).name}", None, Path(path).name, report)
    logging.info(f"Imported {fy_label(report.fiscal_year)} P&L as of {report.as_of} from {path}")
    return 0


def set_opening(conn, fiscal_year: int, amount: Decimal, source: str):
    cur = conn.cursor()
    cur.execute("SELECT opening_balance FROM camel_fiscal_years WHERE fiscal_year = %s", (fiscal_year,))
    old = cur.fetchone()
    cur.execute("""
        INSERT INTO camel_fiscal_years (fiscal_year, opening_balance, source)
        VALUES (%s, %s, %s)
        ON CONFLICT (fiscal_year) DO UPDATE
        SET opening_balance = EXCLUDED.opening_balance, source = EXCLUDED.source, recorded_at = NOW()
    """, (fiscal_year, amount, source))
    was = f" (was {money(old[0])})" if old else ""
    logging.info(f"{fy_label(fiscal_year)} opening balance set to {money(amount)}{was}")


# ---------------------------------------------------------------------------
# Reporting the balance
# ---------------------------------------------------------------------------

def latest_report(cur, fiscal_year=None, before=None):
    conditions, params = ["status = 'imported'"], []
    if fiscal_year is not None:
        conditions.append("fiscal_year = %s")
        params.append(fiscal_year)
    if before is not None:
        conditions.append("as_of_date < %s")
        params.append(before)
    cur.execute(f"""
        SELECT * FROM camel_pl_reports
        WHERE {' AND '.join(conditions)}
        ORDER BY as_of_date DESC, received_at DESC NULLS LAST, id DESC
        LIMIT 1
    """, params)
    return cur.fetchone()


def opening_balance(cur, fiscal_year: int):
    """(amount, source) for a fiscal year, carrying the previous year forward if none is recorded."""
    cur.execute("SELECT opening_balance, source FROM camel_fiscal_years WHERE fiscal_year = %s",
                (fiscal_year,))
    row = cur.fetchone()
    if row:
        return row['opening_balance'], row['source']

    cur.execute("SELECT opening_balance FROM camel_fiscal_years WHERE fiscal_year = %s",
                (fiscal_year - 1,))
    prev_opening = cur.fetchone()
    prev_last = latest_report(cur, fiscal_year=fiscal_year - 1)
    if not prev_opening or not prev_last:
        return None

    amount = prev_opening['opening_balance'] + prev_last['net_income']
    source = (f"carried forward: {fy_label(fiscal_year - 1)} opening + net income through "
              f"{prev_last['as_of_date']} (last {fy_label(fiscal_year - 1)} report seen); "
              f"confirm with Hillel and correct with --set-opening")
    cur.execute("INSERT INTO camel_fiscal_years (fiscal_year, opening_balance, source) VALUES (%s, %s, %s)",
                (fiscal_year, amount, source))
    logging.warning(f"No {fy_label(fiscal_year)} opening balance was recorded; carried forward "
                    f"{money(amount)}. Year-end entries booked after {prev_last['as_of_date']} are "
                    f"missing from it - confirm with Hillel and correct with --set-opening.")
    return amount, source


def account_changes(latest_accounts, previous_accounts):
    """[(section, account, delta)] for every account whose year-to-date total moved."""
    def totals(accounts):
        return {a['account']: (a.get('section'), Decimal(str(a['total']))) for a in accounts or []}

    now, before = totals(latest_accounts), totals(previous_accounts)
    changes = []
    for account in now.keys() | before.keys():
        section = (now.get(account) or before.get(account))[0]
        delta = now.get(account, (None, Decimal('0')))[1] - before.get(account, (None, Decimal('0')))[1]
        if delta:
            changes.append((section, account, delta))
    return sorted(changes, key=lambda c: (c[0] != 'income', -abs(c[2])))


def report_balance(conn):
    """Log the balance. Returns (ok, stale)."""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    latest = latest_report(cur)
    if not latest:
        logging.warning("No P&L report imported yet - no balance to report")
        return True, True

    fy = latest['fiscal_year']
    opening = opening_balance(cur, fy)
    if opening is None:
        logging.error(f"No opening balance recorded for {fy_label(fy)}. Record it from Hillel's "
                      f"roll-forward report:\n  python3 finance/track_balance.py --set-opening "
                      f"{fy_label(fy)} <amount> --source \"...\"")
        return False, False
    amount, source = opening
    balance = amount + latest['net_income']

    logging.info("")
    logging.info(f"The Camel's balance as of {day(latest['as_of_date'])}: {money(balance)}")
    logging.info(f"  {fy_label(fy)} opening balance  {money(amount):>14}   ({source})")
    logging.info(f"  + income this FY      {money(latest['total_income']):>14}")
    logging.info(f"  - expenses this FY    {money(latest['total_expenses']):>14}")

    previous = latest_report(cur, fiscal_year=fy, before=latest['as_of_date'])
    if previous:
        logging.info(f"Since the {day(previous['as_of_date'])} report: "
                     f"income {signed(latest['total_income'] - previous['total_income'])}, "
                     f"expenses {signed(latest['total_expenses'] - previous['total_expenses'])}")
        for section, account, delta in account_changes(latest['accounts'], previous['accounts']):
            kind = 'income' if section in ('income', 'other_income') else 'expense'
            logging.info(f"    {kind:<8} {account:<45} {signed(delta):>14}")

    age = (date.today() - latest['as_of_date']).days
    stale = age > STALE_AFTER_DAYS
    if stale:
        logging.warning(f"The newest P&L is {age} days old. QuickBooks should send one every Sunday "
                        f"('Camel P&L by Month updated weekly') - check the schedule with Hillel.")
    return True, stale


def main() -> int:
    parser = argparse.ArgumentParser(description="Track The Camel's balance from QuickBooks P&L emails")
    parser.add_argument('--dry-run', action='store_true', help='Do everything but roll back all database writes')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--no-gmail', action='store_true', help='Only log the balance from stored reports')
    mode.add_argument('--import-file', metavar='XLSX', help='Import a saved QuickBooks P&L workbook')
    mode.add_argument('--set-opening', nargs=2, metavar=('FISCAL_YEAR', 'AMOUNT'),
                      help="Record a fiscal year's opening balance, e.g. --set-opening FY27 1234.56")
    parser.add_argument('--source', help='Where the --set-opening figure comes from (required with it)')
    args = parser.parse_args()

    opening = None
    if args.set_opening:
        if not args.source:
            parser.error('--set-opening needs --source')
        try:
            opening = (parse_fiscal_year(args.set_opening[0]),
                       Decimal(args.set_opening[1].replace(',', '').replace('$', '')))
        except (ValueError, InvalidOperation):
            parser.error('usage: --set-opening FY27 1234.56')

    load_dotenv()
    gmail_mode = not (args.no_gmail or args.import_file or args.set_opening)
    try:
        conn = get_db_connection()
    except ConnectionError as e:
        logging.error(str(e))
        return 1

    failures = 0
    try:
        check_tables(conn.cursor())
        if opening:
            set_opening(conn, opening[0], opening[1], args.source)
        elif args.import_file:
            failures += import_file(conn, args.import_file)
        elif gmail_mode:
            query = os.getenv('QBO_REPORT_QUERY') or DEFAULT_GMAIL_QUERY
            failures += import_from_gmail(conn, GmailClient.from_env(), query, args.dry_run)

        ok, stale = report_balance(conn)
        if not ok:
            failures += 1

        if args.dry_run:
            conn.rollback()
            logging.info("Dry run - all database writes rolled back")
        else:
            conn.commit()
    except (SetupError, GmailAuthError) as e:
        conn.rollback()
        logging.error(str(e))
        return 1
    finally:
        conn.close()

    if failures or (stale and gmail_mode):
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())

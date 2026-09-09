#!/usr/bin/env python3
"""
Load the Harvard student directory CSV into the `harvard_students` table.

The CSV (mailChimp/harvard_students_with_emails.csv by default) is the
authoritative list of current Harvard students. luma/auto_approve_rsvps.py uses
the table as an extra verification layer: a Harvard-domain email only counts
as a verified Harvard student if it appears in this list.

Why a table and not the CSV itself?
    The GitHub repo that Railway deploys from is PUBLIC, and *.csv is
    gitignored on purpose - the file holds ~4,600 student names and emails and
    must never be committed. The Railway container therefore never sees the
    CSV; it reads the table instead. Run this script locally (it uses the DB
    credentials in .env) every time the CSV is updated.

Usage:
    python3 mailChimp/load_harvard_students.py                 # full refresh from default CSV
    python3 mailChimp/load_harvard_students.py --csv other.csv # different file
    python3 mailChimp/load_harvard_students.py --dry-run       # parse and report only
    python3 mailChimp/load_harvard_students.py --no-replace    # upsert without deleting rows
                                                               # missing from the CSV
"""

import os
import sys
import csv
import argparse
import logging
from collections import Counter
from datetime import datetime, timezone

import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DEFAULT_CSV = os.path.join(SCRIPT_DIR, 'harvard_students_with_emails.csv')

load_dotenv(os.path.join(PROJECT_DIR, '.env'))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS harvard_students (
    email VARCHAR(255) PRIMARY KEY,
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    full_name VARCHAR(200),
    profile_url TEXT,
    source_file VARCHAR(255),
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def get_db_connection():
    try:
        return psycopg2.connect(
            host=os.getenv('PGHOST'),
            port=os.getenv('PGPORT'),
            database=os.getenv('PGDATABASE'),
            user=os.getenv('PGUSER'),
            password=os.getenv('PGPASSWORD'),
        )
    except Exception as e:
        logging.error(f"Failed to connect to database: {e}")
        sys.exit(1)


def _pick_column(fieldnames, *candidates):
    """Find a CSV column by case-insensitive name; returns None if absent."""
    lowered = {(f or '').strip().lower(): f for f in fieldnames}
    for cand in candidates:
        if cand.lower() in lowered:
            return lowered[cand.lower()]
    return None


def read_students(csv_path):
    """
    Parse the CSV into a dict keyed by lowercased email.

    Returns:
        (students: dict[email -> row dict], stats: dict)
    """
    with open(csv_path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            logging.error("CSV has no header row")
            sys.exit(1)

        email_col = _pick_column(reader.fieldnames, 'Email', 'email address', 'e-mail')
        if not email_col:
            logging.error(f"No email column found in CSV header: {reader.fieldnames}")
            sys.exit(1)
        first_col = _pick_column(reader.fieldnames, 'First Name', 'first')
        last_col = _pick_column(reader.fieldnames, 'Last Name', 'last')
        full_col = _pick_column(reader.fieldnames, 'Full Name', 'name')
        url_col = _pick_column(reader.fieldnames, 'Profile URL', 'url')

        rows = list(reader)

    # An email shared by several rows is a directory placeholder (e.g. the
    # IT help desk address listed for students without a public email), not a
    # student's own address. Drop it entirely so it can never verify anyone.
    email_counts = Counter(
        (row.get(email_col) or '').strip().lower() for row in rows
    )
    shared_emails = {e for e, n in email_counts.items() if e and n > 1}
    for e in sorted(shared_emails):
        logging.warning(f"Skipping shared placeholder email {e} ({email_counts[e]} rows)")

    students = {}
    stats = Counter()
    for row in rows:
            stats['rows'] += 1
            email = (row.get(email_col) or '').strip().lower()
            if not email or '@' not in email:
                stats['no_email'] += 1
                continue
            if email in shared_emails:
                stats['shared_placeholder'] += 1
                continue
            students[email] = {
                'email': email,
                'first_name': ((row.get(first_col) if first_col else '') or '').strip()[:100] or None,
                'last_name': ((row.get(last_col) if last_col else '') or '').strip()[:100] or None,
                'full_name': ((row.get(full_col) if full_col else '') or '').strip()[:200] or None,
                'profile_url': ((row.get(url_col) if url_col else '') or '').strip() or None,
            }
            stats['domain:' + email.split('@', 1)[1]] += 1

    return students, stats


def load_students(conn, students, source_file, replace=True):
    """Write the student list to the DB in one transaction."""
    cur = conn.cursor()
    try:
        cur.execute(CREATE_TABLE_SQL)
        if replace:
            cur.execute("DELETE FROM harvard_students")
        loaded_at = datetime.now(timezone.utc)
        rows = [
            (s['email'], s['first_name'], s['last_name'], s['full_name'],
             s['profile_url'], source_file, loaded_at)
            for s in students.values()
        ]
        execute_values(
            cur,
            """
            INSERT INTO harvard_students
                (email, first_name, last_name, full_name, profile_url, source_file, loaded_at)
            VALUES %s
            ON CONFLICT (email) DO UPDATE SET
                first_name = EXCLUDED.first_name,
                last_name = EXCLUDED.last_name,
                full_name = EXCLUDED.full_name,
                profile_url = EXCLUDED.profile_url,
                source_file = EXCLUDED.source_file,
                loaded_at = EXCLUDED.loaded_at
            """,
            rows,
            page_size=1000,
        )
        cur.execute("SELECT COUNT(*) FROM harvard_students")
        total_in_table = cur.fetchone()[0]
        conn.commit()
        return total_in_table
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def main():
    parser = argparse.ArgumentParser(
        description='Load the Harvard student directory CSV into the harvard_students table'
    )
    parser.add_argument('--csv', default=DEFAULT_CSV, help=f'CSV path (default: {DEFAULT_CSV})')
    parser.add_argument('--dry-run', action='store_true', help='Parse and report; do not touch the DB')
    parser.add_argument('--no-replace', action='store_true',
                        help='Upsert only; keep rows that are no longer in the CSV')
    args = parser.parse_args()

    if not os.path.isfile(args.csv):
        logging.error(f"CSV not found: {args.csv}")
        sys.exit(1)

    students, stats = read_students(args.csv)
    logging.info(f"Parsed {args.csv}")
    logging.info(f"  rows in file:        {stats['rows']}")
    logging.info(f"  usable emails:       {len(students)}")
    logging.info(f"  rows without email:  {stats['no_email']}")
    logging.info(f"  duplicate emails:    {stats['duplicate_email']}")
    for key, count in sorted(stats.items()):
        if key.startswith('domain:'):
            logging.info(f"  {key[7:]:<28} {count}")

    if not students:
        logging.error("No usable emails in CSV - refusing to load an empty list")
        sys.exit(1)

    if args.dry_run:
        logging.info("DRY RUN - no database changes made")
        return

    conn = get_db_connection()
    try:
        total = load_students(conn, students, os.path.basename(args.csv), replace=not args.no_replace)
    finally:
        conn.close()

    mode = 'upsert (kept existing rows)' if args.no_replace else 'full refresh'
    logging.info(f"Loaded {len(students)} students into harvard_students ({mode}); table now has {total} rows")


if __name__ == '__main__':
    main()

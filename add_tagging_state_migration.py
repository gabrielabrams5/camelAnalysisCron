#!/usr/bin/env python3
"""
Database Migration: Add attendance_imported_at and mailchimp_tagged_at columns to events table

This migration adds:
- attendance_imported_at (TIMESTAMP): Set when Luma attendance import completes for the event.
  Replaces the old `attendance == 0` heuristic as the "needs processing" gate in luma_sync.py,
  which caused events with genuinely zero check-ins to be re-imported on every run.
- mailchimp_tagged_at (TIMESTAMP): Set when Mailchimp attendee/no-show tagging completes.
  Events with attendance imported but no tag timestamp are retried on every pipeline run,
  so a single failed run no longer loses the tags forever.

Backfill:
- attendance_imported_at = now() for all past events (their Luma data is already in the DB;
  this includes the zero-attendance events that were stuck re-importing every 6 hours).
- mailchimp_tagged_at = now() for all past events EXCEPT event 37 ("The Camel x Christie's"),
  which is the one past event whose tags are missing in Mailchimp. Leaving it NULL lets the
  pipeline's self-healing tag step create its tags on the next run.
"""

import psycopg2
import os
from dotenv import load_dotenv

# The one past event with no Mailchimp tags as of this migration (2026-07-27).
# All other past events were verified to already have their _attended /
# _first_attended / _rsvp_no_show tags in Mailchimp.
UNTAGGED_EVENT_IDS = [37]


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
    """Add tagging-state columns to events table and backfill them."""
    conn = connect_to_db()
    cur = conn.cursor()

    try:
        print("Starting migration: Adding attendance_imported_at and mailchimp_tagged_at columns...")

        print("  - Adding 'attendance_imported_at' column (TIMESTAMP)...")
        cur.execute("""
            ALTER TABLE events
            ADD COLUMN IF NOT EXISTS attendance_imported_at TIMESTAMP;
        """)

        print("  - Adding 'mailchimp_tagged_at' column (TIMESTAMP)...")
        cur.execute("""
            ALTER TABLE events
            ADD COLUMN IF NOT EXISTS mailchimp_tagged_at TIMESTAMP;
        """)

        # Backfill: all past events already have their attendance data in the DB
        print("  - Backfilling attendance_imported_at for past events...")
        cur.execute("""
            UPDATE events
            SET attendance_imported_at = NOW()
            WHERE start_datetime < NOW()
              AND attendance_imported_at IS NULL;
        """)
        print(f"    {cur.rowcount} events marked as imported")

        # Backfill: all past events except the known-untagged ones are already tagged
        print("  - Backfilling mailchimp_tagged_at for already-tagged past events...")
        cur.execute("""
            UPDATE events
            SET mailchimp_tagged_at = NOW()
            WHERE start_datetime < NOW()
              AND mailchimp_tagged_at IS NULL
              AND NOT (id = ANY(%s));
        """, (UNTAGGED_EVENT_IDS,))
        print(f"    {cur.rowcount} events marked as tagged")

        conn.commit()
        print("✓ Migration completed successfully!")

        # Verify
        cur.execute("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'events'
            AND column_name IN ('attendance_imported_at', 'mailchimp_tagged_at');
        """)
        columns = cur.fetchall()
        print("\nVerification:")
        for col in columns:
            print(f"  ✓ Column '{col[0]}' exists with type '{col[1]}'")

        cur.execute("""
            SELECT id, event_name, attendance,
                   attendance_imported_at IS NOT NULL AS imported,
                   mailchimp_tagged_at IS NOT NULL AS tagged
            FROM events
            WHERE start_datetime < NOW()
            ORDER BY id;
        """)
        print("\nPast events state:")
        for row in cur.fetchall():
            print(f"  ID {row[0]:>3} | attendance {row[2]:>3} | imported={row[3]} | tagged={row[4]} | {row[1]}")

    except psycopg2.Error as e:
        print(f"✗ Migration failed: {e}")
        conn.rollback()
        raise

    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    run_migration()

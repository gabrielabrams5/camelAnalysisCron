#!/usr/bin/env python3
"""
Database Migration: Add mailchimp_synced_at column to people table

This migration adds:
- mailchimp_synced_at (TIMESTAMP): Set when sync_mailchimp_audience.py has pushed
  the person to the Mailchimp audience. The sync now only processes people where
  this is NULL (new Google Form signups, first-time Luma RSVPs) instead of
  re-pushing every person in the database on every run. Re-pushing everyone
  undid manual curation in Mailchimp: people deliberately left out of the
  audience (e.g. moved to the NYC audience) were re-added as subscribed.

Backfill:
- mailchimp_synced_at = NOW() for every existing person. As of this migration
  (2026-10-20) the "Camel" audience was rebuilt by hand to contain exactly the
  people that should be on it, so nobody currently in the database needs
  pushing. Run this AFTER that rebuild, BEFORE the next pipeline run.
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
    """Add mailchimp_synced_at to people and mark everyone existing as synced."""
    conn = connect_to_db()
    cur = conn.cursor()

    try:
        print("Starting migration: Adding mailchimp_synced_at column to people...")

        cur.execute("""
            ALTER TABLE people
            ADD COLUMN IF NOT EXISTS mailchimp_synced_at TIMESTAMP;
        """)

        print("  - Backfilling mailchimp_synced_at for existing people...")
        cur.execute("""
            UPDATE people
            SET mailchimp_synced_at = NOW()
            WHERE mailchimp_synced_at IS NULL;
        """)
        print(f"    {cur.rowcount} people marked as synced")

        conn.commit()
        print("✓ Migration completed successfully!")

        # Verify
        cur.execute("""
            SELECT COUNT(*) FILTER (WHERE mailchimp_synced_at IS NOT NULL),
                   COUNT(*) FILTER (WHERE mailchimp_synced_at IS NULL)
            FROM people;
        """)
        synced, unsynced = cur.fetchone()
        print(f"\nVerification: {synced} synced, {unsynced} unsynced")

    except psycopg2.Error as e:
        print(f"✗ Migration failed: {e}")
        conn.rollback()
        raise

    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    run_migration()

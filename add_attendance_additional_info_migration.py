#!/usr/bin/env python3
"""
Database Migration: Add additional_info column to attendance table

Before: registration answers were stored as a single JSON snapshot on
people.additional_info, overwritten on every new registration. That meant
cross-event aggregations of "what brings people to Camel" collapsed to each
person's most-recent answer rather than their answer at the time of each event.

After: each attendance row carries its own additional_info snapshot, so every
event preserves the answers given at that registration.
"""

import psycopg2
import os
from dotenv import load_dotenv


def connect_to_db():
    load_dotenv()
    return psycopg2.connect(
        host=os.getenv('PGHOST'),
        port=os.getenv('PGPORT'),
        database=os.getenv('PGDATABASE'),
        user=os.getenv('PGUSER'),
        password=os.getenv('PGPASSWORD'),
    )


def run_migration():
    conn = connect_to_db()
    cur = conn.cursor()

    try:
        print("Adding additional_info JSONB column to attendance table...")
        cur.execute("""
            ALTER TABLE attendance
            ADD COLUMN IF NOT EXISTS additional_info JSONB;
        """)
        conn.commit()
        print("Migration completed successfully.")

        cur.execute("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'attendance' AND column_name = 'additional_info';
        """)
        for col in cur.fetchall():
            print(f"  verified: {col[0]} ({col[1]})")

    except psycopg2.Error as e:
        print(f"Migration failed: {e}")
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    run_migration()

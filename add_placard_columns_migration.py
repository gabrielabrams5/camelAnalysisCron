#!/usr/bin/env python3
"""
Database Migration: Add cost and placard_pdf columns to events table

This migration adds:
- cost (NUMERIC): Optional event cost for financial tracking
- placard_pdf (BYTEA): Binary storage for generated event infographic PDFs
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
    """Add cost and placard_pdf columns to events table."""
    conn = connect_to_db()
    cur = conn.cursor()

    try:
        print("Starting migration: Adding cost and placard_pdf columns to events table...")

        # Add cost column (NUMERIC for flexible precision)
        print("  - Adding 'cost' column (NUMERIC)...")
        cur.execute("""
            ALTER TABLE events
            ADD COLUMN IF NOT EXISTS cost NUMERIC;
        """)

        # Add placard_pdf column (BYTEA for binary PDF storage)
        print("  - Adding 'placard_pdf' column (BYTEA)...")
        cur.execute("""
            ALTER TABLE events
            ADD COLUMN IF NOT EXISTS placard_pdf BYTEA;
        """)

        # Commit changes
        conn.commit()
        print("✓ Migration completed successfully!")

        # Verify columns were added
        cur.execute("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'events'
            AND column_name IN ('cost', 'placard_pdf');
        """)

        columns = cur.fetchall()
        print("\nVerification:")
        for col in columns:
            print(f"  ✓ Column '{col[0]}' exists with type '{col[1]}'")

    except psycopg2.Error as e:
        print(f"✗ Migration failed: {e}")
        conn.rollback()
        raise

    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    run_migration()

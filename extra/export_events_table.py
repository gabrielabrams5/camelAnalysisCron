#!/usr/bin/env python3
"""
Export events table from database to CSV
Exports all events with their details
"""

import os
import sys
import psycopg2
import pandas as pd
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Validate required environment variables
required_env_vars = ['PGHOST', 'PGPORT', 'PGDATABASE', 'PGUSER', 'PGPASSWORD']
missing_vars = [var for var in required_env_vars if not os.getenv(var)]
if missing_vars:
    print(f"Error: Missing required environment variables: {', '.join(missing_vars)}")
    sys.exit(1)


def get_db_connection():
    """
    Establish connection to Railway PostgreSQL database using environment variables.

    Returns:
        psycopg2 connection object

    Raises:
        ConnectionError: If connection fails
    """
    try:
        conn = psycopg2.connect(
            host=os.getenv('PGHOST'),
            port=os.getenv('PGPORT'),
            database=os.getenv('PGDATABASE'),
            user=os.getenv('PGUSER'),
            password=os.getenv('PGPASSWORD')
        )
        return conn
    except psycopg2.Error as e:
        raise ConnectionError(f"Failed to connect to database: {e}")


def export_events_table():
    """
    Export all events from the events table to CSV
    """
    print("=" * 60)
    print("EVENTS TABLE EXPORTER")
    print("=" * 60)

    conn = None

    try:
        # Connect to database
        print("\nConnecting to database...")
        conn = get_db_connection()
        print("Connected successfully")

        # Query all events
        print("\nFetching events from database...")
        query = """
            SELECT
                id,
                event_name,
                category,
                location,
                start_datetime,
                description
            FROM events
            ORDER BY start_datetime DESC
        """

        # Load into DataFrame
        df = pd.read_sql(query, conn)
        print(f"Found {len(df)} events")

        # Create test_output directory at project root (same level as extra/)
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(script_dir)  # Go up one level from extra/
        output_dir = os.path.join(project_root, "test_output")
        os.makedirs(output_dir, exist_ok=True)

        # Generate filename
        output_filename = os.path.join(output_dir, "events_table.csv")

        # Export to CSV
        print(f"\nExporting to CSV...")
        df.to_csv(output_filename, index=False)

        # Display summary
        print("\n" + "=" * 60)
        print("EXPORT COMPLETE")
        print("=" * 60)
        print(f"Total events exported: {len(df)}")
        print(f"Output file: {output_filename}")
        print(f"\nColumns included:")
        for col in df.columns:
            print(f"  - {col}")
        print("=" * 60)

    except psycopg2.Error as e:
        print(f"\nDatabase error: {e}")
        sys.exit(1)

    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)

    finally:
        if conn:
            conn.close()
            print("\nDatabase connection closed")


if __name__ == '__main__':
    export_events_table()

#!/usr/bin/env python3
"""
Count upper classmen attendance vs total attendance for two time periods.
- Period 1: September 2024 - March 5, 2025
- Period 2: September 2025 - March 5, 2026
Counts total check-ins (not unique individuals).
Excludes attendees with null class years.
"""

import os
import sys
import psycopg2
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def get_db_connection():
    """Establish connection to Railway PostgreSQL database."""
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
        print(f"Error: Failed to connect to database: {e}", file=sys.stderr)
        sys.exit(1)

def get_period_stats(conn, start_date, end_date, upperclass_years):
    """
    Get attendance statistics for a specific time period.

    Args:
        conn: Database connection
        start_date: Start date string (inclusive)
        end_date: End date string (exclusive - day after actual end)
        upperclass_years: Tuple of class years considered upper classmen

    Returns:
        Tuple of (upperclassmen_count, total_count)
    """
    query = """
        SELECT
            COUNT(CASE WHEN p.class_year IN %s THEN a.id END) as upperclassmen_count,
            COUNT(a.id) as total_count
        FROM attendance a
        INNER JOIN people p ON a.person_id = p.id
        INNER JOIN events e ON a.event_id = e.id
        WHERE a.checked_in = true
            AND p.class_year IS NOT NULL
            AND e.start_datetime >= %s
            AND e.start_datetime < %s
    """

    try:
        cursor = conn.cursor()
        cursor.execute(query, (upperclass_years, start_date, end_date))
        result = cursor.fetchone()
        cursor.close()
        return result
    except Exception as e:
        print(f"Error executing query: {e}", file=sys.stderr)
        sys.exit(1)

def main():
    """Main function to run the upper classmen attendance analysis."""
    print("=" * 64)
    print("UPPER CLASSMEN ATTENDANCE ANALYSIS")
    print("=" * 64)
    print()

    conn = get_db_connection()

    try:
        # Period 1: September 2024 - March 5, 2025
        # Upper classmen: Class of 2025 (Seniors), Class of 2026 (Juniors)
        period1_upper, period1_total = get_period_stats(
            conn,
            '2024-09-01 00:00:00',
            '2025-03-06 00:00:00',
            (2025, 2026)
        )

        # Period 2: September 2025 - March 5, 2026
        # Upper classmen: Class of 2026 (Seniors), Class of 2027 (Juniors)
        period2_upper, period2_total = get_period_stats(
            conn,
            '2025-09-01 00:00:00',
            '2026-03-06 00:00:00',
            (2026, 2027)
        )

        # Display Period 1 results
        print("Period 1: September 2024 - March 5, 2025")
        print("-" * 64)
        if period1_total > 0:
            period1_pct = (period1_upper / period1_total) * 100
            print(f"  Upper Classmen (Class 2025-2026):  {period1_upper:,} check-ins")
            print(f"  Total Attendees:                    {period1_total:,} check-ins")
            print(f"  Percentage:                         {period1_pct:.2f}%")
        else:
            print("  No attendance data found for this period.")
        print()

        # Display Period 2 results
        print("Period 2: September 2025 - March 5, 2026")
        print("-" * 64)
        if period2_total > 0:
            period2_pct = (period2_upper / period2_total) * 100
            print(f"  Upper Classmen (Class 2026-2027):  {period2_upper:,} check-ins")
            print(f"  Total Attendees:                    {period2_total:,} check-ins")
            print(f"  Percentage:                         {period2_pct:.2f}%")
        else:
            print("  No attendance data found for this period.")
        print()

        print("=" * 64)
        print("Note: Counts are total check-ins, not unique individuals.")
        print("Only includes attendees with non-null class years.")
        print("=" * 64)

    finally:
        conn.close()

if __name__ == "__main__":
    main()

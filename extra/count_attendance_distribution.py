#!/usr/bin/env python3
"""
Count and display how many people have attended 1, 2, 3, 4+ events.
Lists the first and last names of people in each attendance group.
Filters to only include class years 2026-2029 and events since September 2025.
"""

import os
import sys
import psycopg2
import pandas as pd
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

def get_attendance_distribution():
    """Get all people with their attendance counts (class years 2026-2029, events since Sept 2025)."""
    conn = get_db_connection()

    query = """
        SELECT
            p.first_name,
            p.last_name,
            COUNT(a.id) AS event_attendance_count
        FROM people p
        INNER JOIN attendance a ON p.id = a.person_id
        INNER JOIN events e ON a.event_id = e.id
        WHERE a.checked_in = true
            AND e.start_datetime >= '2025-09-01 00:00:00'
            AND p.class_year >= 2026
            AND p.class_year <= 2029
        GROUP BY p.id, p.first_name, p.last_name
        HAVING COUNT(a.id) > 0
        ORDER BY event_attendance_count, p.last_name, p.first_name
    """

    try:
        df = pd.read_sql(query, conn)
        return df
    except Exception as e:
        print(f"Error executing query: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()

def main():
    """Main function to run the attendance distribution script."""
    print("=" * 60)
    print("ATTENDANCE DISTRIBUTION")
    print("Class Years 2026-2029 | Events Since Sept 2025")
    print("=" * 60)
    print()

    # Get data
    df = get_attendance_distribution()

    if df.empty:
        print("No attendance data found.")
        return

    # Group by attendance count
    grouped = df.groupby('event_attendance_count')

    # Display results
    total_people = 0
    for count, group in grouped:
        num_people = len(group)
        total_people += num_people

        # Singular/plural
        event_word = "event" if count == 1 else "events"
        people_word = "person" if num_people == 1 else "people"

        print(f"Attended {count} {event_word} ({num_people} {people_word}):")

        # List all people in this group
        for _, row in group.iterrows():
            print(f"  - {row['first_name']} {row['last_name']}")

        print()

    print("=" * 60)
    print(f"Total people with attendance: {total_people}")
    print("=" * 60)

if __name__ == "__main__":
    main()

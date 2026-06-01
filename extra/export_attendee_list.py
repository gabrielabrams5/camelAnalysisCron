#!/usr/bin/env python3
"""
Export checked-in attendee list from database to CSV
Exports: first_name, last_name, email, phone_number,
and all registration form answers as separate columns
"""

import os
import sys
import re
import json
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


def get_recent_events(limit=5):
    """
    Fetch recent events from database

    Args:
        limit: Number of recent events to return (default 5)

    Returns:
        List of event dictionaries sorted by date (most recent first)
    """
    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        query = """
            SELECT id, event_name, start_datetime
            FROM events
            ORDER BY start_datetime DESC
            LIMIT %s
        """

        cursor.execute(query, (limit,))
        rows = cursor.fetchall()

        events = []
        for row in rows:
            event_id, event_name, start_datetime = row
            events.append({
                'id': event_id,
                'name': event_name,
                'start_datetime': start_datetime
            })

        return events

    except psycopg2.Error as e:
        print(f"Error fetching events: {e}")
        sys.exit(1)

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def get_event_attendees(event_id):
    """
    Fetch all checked-in attendees for an event from database

    Args:
        event_id: Database event ID

    Returns:
        List of attendee dictionaries with person data and additional_info
    """
    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        print("\nFetching attendees...")

        query = """
            SELECT
                p.first_name,
                p.last_name,
                COALESCE(p.school_email, p.personal_email) as email,
                p.phone_number,
                p.additional_info
            FROM attendance a
            JOIN people p ON a.person_id = p.id
            WHERE a.event_id = %s
              AND a.checked_in = TRUE
            ORDER BY p.last_name, p.first_name
        """

        cursor.execute(query, (event_id,))
        rows = cursor.fetchall()

        attendees = []
        for row in rows:
            first_name, last_name, email, phone_number, additional_info = row

            attendees.append({
                'first_name': first_name,
                'last_name': last_name,
                'email': email or '',
                'phone_number': phone_number or '',
                'additional_info': additional_info
            })

        print(f"  Found {len(attendees)} checked-in attendees")

        return attendees

    except psycopg2.Error as e:
        print(f"Error fetching attendees: {e}")
        sys.exit(1)

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def parse_additional_info(additional_info):
    """
    Parse additional_info JSON field

    Args:
        additional_info: JSON string, dict, or None

    Returns:
        Dictionary of registration answers
    """
    if additional_info is None:
        return {}
    if isinstance(additional_info, dict):
        return additional_info
    if isinstance(additional_info, str):
        try:
            return json.loads(additional_info) if additional_info and additional_info != 'null' else {}
        except json.JSONDecodeError:
            return {}
    return {}


def extract_attendee_data(attendees):
    """
    Extract attendee data and all registration answers from additional_info

    Args:
        attendees: List of attendee dictionaries from database

    Returns:
        pandas DataFrame with attendee data
    """
    attendee_records = []

    for attendee in attendees:
        # Extract base fields
        record = {
            'first_name': attendee.get('first_name', ''),
            'last_name': attendee.get('last_name', ''),
            'email': attendee.get('email', ''),
            'phone_number': attendee.get('phone_number', '')
        }

        # Parse additional_info JSON and extract all custom answers
        additional_info = parse_additional_info(attendee.get('additional_info'))

        # Add all custom registration answers as separate columns
        if isinstance(additional_info, dict):
            for question_label, answer_value in additional_info.items():
                if question_label and answer_value is not None:
                    record[question_label] = answer_value

        attendee_records.append(record)

    # Convert to DataFrame
    df = pd.DataFrame(attendee_records)

    return df


def sanitize_filename(name):
    """
    Sanitize event name for use in filename

    Args:
        name: Event name string

    Returns:
        Sanitized filename string
    """
    # Replace spaces and special characters with underscores
    sanitized = re.sub(r'[^\w\s-]', '', name)
    sanitized = re.sub(r'[\s-]+', '_', sanitized)
    return sanitized.lower()


def export_attendee_list():
    """
    Main function to export attendee list
    """
    print("=" * 60)
    print("DATABASE ATTENDEE LIST EXPORTER")
    print("=" * 60)

    # Fetch recent events
    print("\nFetching recent events...")
    events = get_recent_events(limit=5)

    if not events:
        print("No events found.")
        sys.exit(0)

    # Display events
    print(f"\nFound {len(events)} recent events:\n")
    for i, event in enumerate(events, 1):
        # Format date
        start_date = event['start_datetime'].strftime('%Y-%m-%d') if event['start_datetime'] else 'Unknown date'
        print(f"  {i}. {event['name']}")
        print(f"     Date: {start_date}")
        print()

    # Get user selection
    while True:
        try:
            selection = input(f"Select an event (1-{len(events)}): ").strip()
            selection_num = int(selection)

            if 1 <= selection_num <= len(events):
                selected_event = events[selection_num - 1]
                break
            else:
                print(f"Please enter a number between 1 and {len(events)}")
        except ValueError:
            print("Please enter a valid number")
        except KeyboardInterrupt:
            print("\n\nCancelled by user")
            sys.exit(0)

    print(f"\nSelected: {selected_event['name']}")

    # Fetch attendees
    attendees = get_event_attendees(selected_event['id'])

    if not attendees:
        print("\nNo checked-in attendees found for this event.")
        sys.exit(0)

    # Extract attendee data
    print("\nExtracting attendee data...")
    df = extract_attendee_data(attendees)

    # Create test_output directory at project root (same level as extra/)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)  # Go up one level from extra/
    output_dir = os.path.join(project_root, "test_output")
    os.makedirs(output_dir, exist_ok=True)

    # Generate filename
    sanitized_name = sanitize_filename(selected_event['name'])
    output_filename = os.path.join(output_dir, f"{sanitized_name}_attendee_list.csv")

    # Export to CSV
    df.to_csv(output_filename, index=False)

    # Display summary
    print("\n" + "=" * 60)
    print("EXPORT COMPLETE")
    print("=" * 60)
    print(f"Event: {selected_event['name']}")
    print(f"Checked-in attendees exported: {len(attendees)}")
    print(f"Output file: {output_filename}")
    print(f"Columns: {len(df.columns)}")
    print(f"\nColumns included:")
    for col in df.columns:
        print(f"  - {col}")
    print("=" * 60)


if __name__ == '__main__':
    export_attendee_list()

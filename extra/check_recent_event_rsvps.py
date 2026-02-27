#!/usr/bin/env python3
"""
Check Recent Event RSVPs
Analyzes the most recent Luma event to determine:
- % of RSVPs who aren't in the people database
- % of RSVPs who are in the database but haven't attended an event

Note: Includes ALL RSVPs regardless of approval status (approved + pending)
"""

import os
import sys
import logging
import requests
import psycopg2
from datetime import datetime
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Load environment variables
load_dotenv()

# Database connection parameters
DB_CONFIG = {
    'host': os.getenv('PGHOST'),
    'port': os.getenv('PGPORT'),
    'database': os.getenv('PGDATABASE'),
    'user': os.getenv('PGUSER'),
    'password': os.getenv('PGPASSWORD')
}

# Luma API configuration
LUMA_API_KEY = os.getenv('LUMA_API_KEY')
LUMA_CALENDAR_ID = os.getenv('LUMA_CALENDAR_ID')
LUMA_API_BASE_URL = 'https://public-api.luma.com/v1'


def get_db_connection():
    """Create and return a database connection"""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        return conn
    except Exception as e:
        logging.error(f"Failed to connect to database: {e}")
        sys.exit(1)


def fetch_all_events():
    """
    Fetch all events from Luma API

    Returns:
        List of event dictionaries
    """
    headers = {
        'Authorization': f'Bearer {LUMA_API_KEY}',
        'Content-Type': 'application/json'
    }

    url = f'{LUMA_API_BASE_URL}/calendar/list-events'
    params = {}

    if LUMA_CALENDAR_ID:
        params['calendar_api_id'] = LUMA_CALENDAR_ID

    try:
        response = requests.get(url, headers=headers, params=params, timeout=60)
        response.raise_for_status()
        data = response.json()

        entries = data.get('entries', [])
        logging.info(f"Fetched {len(entries)} events from Luma API")
        return entries
    except Exception as e:
        logging.error(f"Failed to fetch events from Luma: {e}")
        sys.exit(1)


def get_most_recent_event(events):
    """
    Find the most recent event by start_at timestamp

    Args:
        events: List of event entries from Luma API

    Returns:
        Most recent event entry
    """
    if not events:
        logging.error("No events found")
        sys.exit(1)

    # Sort events by start_at timestamp (most recent first)
    sorted_events = sorted(
        events,
        key=lambda x: datetime.fromisoformat(x['event']['start_at'].replace('Z', '+00:00')),
        reverse=True
    )

    return sorted_events[0]


def fetch_event_rsvps_by_status(event_api_id, approval_status):
    """
    Fetch RSVPs/guests for a specific Luma event filtered by approval status

    Args:
        event_api_id: Luma event API ID (e.g., 'evt-JJ3lGn0K2BgQy4t')
        approval_status: Filter by approval status ('approved' or 'pending_approval')

    Returns:
        List of guest dictionaries
    """
    headers = {
        'Authorization': f'Bearer {LUMA_API_KEY}',
        'Content-Type': 'application/json'
    }

    url = f'{LUMA_API_BASE_URL}/event/get-guests'
    all_entries = []
    next_cursor = None

    while True:
        params = {
            'event_api_id': event_api_id,
            'approval_status': approval_status
        }
        if next_cursor:
            params['pagination_cursor'] = next_cursor

        try:
            response = requests.get(url, headers=headers, params=params, timeout=60)
            response.raise_for_status()
            data = response.json()

            entries = data.get('entries', [])
            all_entries.extend(entries)

            if not data.get('has_more', False):
                break

            next_cursor = data.get('next_cursor')
            if not next_cursor:
                break
        except Exception as e:
            logging.error(f"Failed to fetch RSVPs with status {approval_status}: {e}")
            break

    return all_entries


def fetch_event_rsvps(event_api_id):
    """
    Fetch all RSVPs/guests for a specific Luma event (approved + pending)

    Args:
        event_api_id: Luma event API ID (e.g., 'evt-JJ3lGn0K2BgQy4t')

    Returns:
        List of guest dictionaries
    """
    # Fetch approved RSVPs
    logging.info(f"Fetching approved RSVPs for event: {event_api_id}")
    approved = fetch_event_rsvps_by_status(event_api_id, 'approved')
    logging.info(f"  - Found {len(approved)} approved RSVPs")

    # Fetch pending RSVPs
    logging.info(f"Fetching pending RSVPs for event: {event_api_id}")
    pending = fetch_event_rsvps_by_status(event_api_id, 'pending_approval')
    logging.info(f"  - Found {len(pending)} pending RSVPs")

    # Combine both lists
    all_entries = approved + pending
    logging.info(f"Total RSVPs (approved + pending): {len(all_entries)}")

    return all_entries


def check_person_in_db(cursor, email):
    """
    Check if a person exists in the database by email

    Args:
        cursor: Database cursor
        email: Email address to search for

    Returns:
        Person record dict with id and event_attendance_count, or None
    """
    if not email:
        return None

    email = email.lower().strip()

    cursor.execute("""
        SELECT id, first_name, last_name, school_email, personal_email,
               event_attendance_count
        FROM people
        WHERE LOWER(school_email) = %s OR LOWER(personal_email) = %s
    """, (email, email))

    result = cursor.fetchone()
    if result:
        return {
            'id': result[0],
            'first_name': result[1],
            'last_name': result[2],
            'school_email': result[3],
            'personal_email': result[4],
            'attendance_count': result[5] or 0  # Handle NULL values
        }
    return None


def main():
    """Main entry point"""
    # Fetch all events from Luma
    logging.info("Fetching all events from Luma API...")
    events = fetch_all_events()

    if not events:
        logging.error("No events found")
        return

    # Sort events by date (most recent first)
    sorted_events = sorted(
        events,
        key=lambda x: datetime.fromisoformat(x['event']['start_at'].replace('Z', '+00:00')),
        reverse=True
    )

    # Display all events for selection
    print(f"\n{'='*70}")
    print("Available Events")
    print(f"{'='*70}\n")

    for i, entry in enumerate(sorted_events, 1):
        event = entry['event']
        name = event.get('name', 'Unknown Event')
        start = event.get('start_at', 'Unknown Date')
        # Format the date nicely
        try:
            dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
            formatted_date = dt.strftime('%B %d, %Y at %I:%M %p')
        except:
            formatted_date = start

        print(f"{i}. {name}")
        print(f"   {formatted_date}")
        print()

    # Prompt user to select an event
    while True:
        try:
            selection = input(f"Select an event (1-{len(sorted_events)}) or 'q' to quit: ").strip()

            if selection.lower() == 'q':
                print("Exiting...")
                return

            event_num = int(selection)
            if 1 <= event_num <= len(sorted_events):
                selected_event = sorted_events[event_num - 1]
                break
            else:
                print(f"Please enter a number between 1 and {len(sorted_events)}")
        except ValueError:
            print("Invalid input. Please enter a number or 'q' to quit.")
        except KeyboardInterrupt:
            print("\nExiting...")
            return

    event_api_id = selected_event['api_id']
    event_info = selected_event['event']
    event_name = event_info.get('name', 'Unknown Event')
    event_start = event_info.get('start_at', 'Unknown Date')

    logging.info(f"\nAnalyzing event: {event_name} ({event_start})")

    # Fetch RSVPs for the most recent event (approved + pending)
    rsvps = fetch_event_rsvps(event_api_id)

    if not rsvps:
        logging.warning("No RSVPs found for this event")
        return

    # Connect to database
    conn = get_db_connection()
    cursor = conn.cursor()

    # Categorize RSVPs
    not_in_db = []
    in_db_never_attended = []
    in_db_has_attended = []

    # Track approval status
    approved_count = 0
    pending_count = 0
    other_status_count = 0

    for entry in rsvps:
        guest = entry.get('guest', {})
        email = guest.get('email')
        first_name = guest.get('user_first_name', '')
        last_name = guest.get('user_last_name', '')
        approval_status = guest.get('approval_status', '').lower()

        # Track approval status
        if approval_status == 'approved':
            approved_count += 1
        elif approval_status in ['pending', 'pending_approval']:
            pending_count += 1
        else:
            other_status_count += 1

        person = check_person_in_db(cursor, email)

        if person is None:
            # Not in database at all
            not_in_db.append({
                'email': email,
                'name': f"{first_name} {last_name}",
                'approval_status': approval_status
            })
        elif person['attendance_count'] == 0:
            # In database but never attended an event
            in_db_never_attended.append({
                'email': email,
                'name': f"{first_name} {last_name}",
                'db_id': person['id'],
                'approval_status': approval_status
            })
        else:
            # In database and has attended before
            in_db_has_attended.append({
                'email': email,
                'name': f"{first_name} {last_name}",
                'db_id': person['id'],
                'attendance_count': person['attendance_count'],
                'approval_status': approval_status
            })

    # Calculate percentages
    total_rsvps = len(rsvps)
    not_in_db_count = len(not_in_db)
    never_attended_count = len(in_db_never_attended)
    has_attended_count = len(in_db_has_attended)

    not_in_db_pct = (not_in_db_count / total_rsvps * 100) if total_rsvps > 0 else 0
    never_attended_pct = (never_attended_count / total_rsvps * 100) if total_rsvps > 0 else 0
    has_attended_pct = (has_attended_count / total_rsvps * 100) if total_rsvps > 0 else 0

    # Print results
    print(f"\n{'='*70}")
    print(f"RSVP Analysis for Most Recent Event")
    print(f"{'='*70}")
    print(f"Event: {event_name}")
    print(f"Date: {event_start}")
    print(f"Event ID: {event_api_id}")
    print(f"{'='*70}\n")

    print(f"Total RSVPs: {total_rsvps}")
    print(f"  - Approved: {approved_count}")
    print(f"  - Pending: {pending_count}")
    if other_status_count > 0:
        print(f"  - Other: {other_status_count}")
    print()

    print(f"Not in database: {not_in_db_count} ({not_in_db_pct:.1f}%)")
    print(f"In database but never attended: {never_attended_count} ({never_attended_pct:.1f}%)")
    print(f"Have attended before: {has_attended_count} ({has_attended_pct:.1f}%)")

    print(f"\n{'='*70}\n")

    # Close database connection
    cursor.close()
    conn.close()


if __name__ == '__main__':
    main()

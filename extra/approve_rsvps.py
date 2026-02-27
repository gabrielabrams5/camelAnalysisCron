#!/usr/bin/env python3
"""
Script to manage pending RSVPs for Luma events.
Auto-approves RSVPs from @college.harvard.edu or @mit.edu emails.
Prompts for manual approval/decline of other RSVPs.
"""

import os
import sys
import psycopg2
import requests
from dotenv import load_dotenv
from datetime import datetime, timedelta

# Load environment variables
load_dotenv()

# Database configuration
DB_CONFIG = {
    'host': os.getenv('PGHOST'),
    'port': os.getenv('PGPORT'),
    'database': os.getenv('PGDATABASE'),
    'user': os.getenv('PGUSER'),
    'password': os.getenv('PGPASSWORD')
}

# Luma API configuration
LUMA_API_KEY = os.getenv('LUMA_API_KEY')
LUMA_API_BASE_URL = 'https://public-api.luma.com/v1'

# Approved email domains
APPROVED_DOMAINS = ['@college.harvard.edu', '@mit.edu']


def get_db_connection():
    """Create and return a database connection."""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        return conn
    except Exception as e:
        print(f"Error: Failed to connect to database: {e}")
        sys.exit(1)


def fetch_recent_events(limit=10):
    """
    Fetch the most recent events from the database.

    Args:
        limit: Maximum number of events to return

    Returns:
        List of tuples: (id, event_name, luma_event_id, start_datetime)
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT id, event_name, luma_event_id, start_datetime
            FROM events
            WHERE luma_event_id IS NOT NULL
            ORDER BY start_datetime DESC
            LIMIT %s
        """, (limit,))

        events = cursor.fetchall()
        return events
    finally:
        cursor.close()
        conn.close()


def fetch_pending_rsvps(event_api_id):
    """
    Fetch all pending RSVPs for a specific Luma event.

    Args:
        event_api_id: Luma event API ID (e.g., 'evt-JJ3lGn0K2BgQy4t')

    Returns:
        List of guest dictionaries with pending approval status
    """
    headers = {
        'Authorization': f'Bearer {LUMA_API_KEY}',
        'Content-Type': 'application/json'
    }

    url = f'{LUMA_API_BASE_URL}/event/get-guests'
    all_guests = []
    next_cursor = None

    print(f"Fetching pending RSVPs from Luma API...")

    while True:
        params = {
            'event_api_id': event_api_id,
            'approval_status': 'pending_approval'
        }
        if next_cursor:
            params['pagination_cursor'] = next_cursor

        try:
            response = requests.get(url, headers=headers, params=params, timeout=60)
            response.raise_for_status()
            data = response.json()

            entries = data.get('entries', [])
            all_guests.extend(entries)

            if not data.get('has_more', False):
                break

            next_cursor = data.get('next_cursor')
        except requests.exceptions.RequestException as e:
            print(f"Error fetching pending RSVPs: {e}")
            return []

    print(f"Found {len(all_guests)} pending RSVPs")
    return all_guests


def approve_guest(event_id, guest_email):
    """
    Approve a guest for an event via Luma API.

    Args:
        event_id: Luma event API ID (e.g., evt-xxxxx)
        guest_email: Guest email address

    Returns:
        API response JSON
    """
    url = f"{LUMA_API_BASE_URL}/event/update-guest-status"
    headers = {
        'Authorization': f'Bearer {LUMA_API_KEY}',
        'accept': 'application/json',
        'content-type': 'application/json'
    }
    payload = {
        "guest": {
            "type": "email",
            "email": guest_email
        },
        "status": "approved",
        "event_api_id": event_id
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as e:
        # Print detailed error information
        print(f"  API Error Response: {r.text}")
        print(f"  Payload sent: {payload}")
        raise


def decline_guest(event_id, guest_email):
    """
    Decline a guest for an event via Luma API.

    Args:
        event_id: Luma event API ID (e.g., evt-xxxxx)
        guest_email: Guest email address

    Returns:
        API response JSON
    """
    url = f"{LUMA_API_BASE_URL}/event/update-guest-status"
    headers = {
        'Authorization': f'Bearer {LUMA_API_KEY}',
        'accept': 'application/json',
        'content-type': 'application/json'
    }
    payload = {
        "guest": {
            "type": "email",
            "email": guest_email
        },
        "status": "declined",
        "event_api_id": event_id,
        "should_refund": False
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as e:
        # Print detailed error information
        print(f"  API Error Response: {r.text}")
        print(f"  Payload sent: {payload}")
        raise


def get_registration_answer(guest_data, question_label, case_sensitive=False):
    """
    Extract answer from registration_answers array by question label.

    Args:
        guest_data: Guest data dictionary from Luma API
        question_label: The question label to search for
        case_sensitive: If True, use exact case matching (default: False)

    Returns:
        The answer value if found, None otherwise
    """
    registration_answers = guest_data.get('registration_answers', [])

    for answer in registration_answers:
        answer_label = answer.get('label', '')

        if case_sensitive:
            if answer_label == question_label:
                return answer.get('value')
        else:
            if answer_label.lower() == question_label.lower():
                return answer.get('value')

    return None


def check_approved_email(guest_data):
    """
    Check if guest has an approved email domain in either main email or School Email field.

    Args:
        guest_data: Guest data dictionary from Luma API

    Returns:
        Tuple of (is_approved: bool, matching_email: str or None)
    """
    # Check main email
    main_email = guest_data.get('email', '').lower()
    for domain in APPROVED_DOMAINS:
        if domain in main_email:
            return (True, main_email)

    # Check School Email (.edu) custom field
    school_email = get_registration_answer(guest_data, 'School email (.edu)')
    if school_email:
        school_email_lower = school_email.lower()
        for domain in APPROVED_DOMAINS:
            if domain in school_email_lower:
                return (True, school_email)

    return (False, None)


def get_time_filter_mode():
    """
    Prompt user to select a time filter mode.

    Returns:
        datetime object representing the cutoff time, or None for 'any'
    """
    print("\n" + "="*80)
    print("TIME FILTER MODE")
    print("="*80)
    print("Auto-approve RSVPs from Harvard/MIT users who RSVPed at least X hours ago:")
    print()
    print("1. any   - No time filter (approve all)")
    print("2. 1hr   - RSVPed 1+ hours ago")
    print("3. 12hr  - RSVPed 12+ hours ago")
    print("4. 24hr  - RSVPed 24+ hours ago")
    print("5. 48hr  - RSVPed 48+ hours ago")
    print()

    mode_map = {
        '1': ('any', None),
        '2': ('1hr', 1),
        '3': ('12hr', 12),
        '4': ('24hr', 24),
        '5': ('48hr', 48)
    }

    while True:
        selection = input("Select time filter mode (1-5): ").strip()

        if selection in mode_map:
            mode_name, hours = mode_map[selection]
            if hours is None:
                print(f"\nMode: {mode_name} (no time restriction)")
                return None
            else:
                cutoff_time = datetime.utcnow() - timedelta(hours=hours)
                print(f"\nMode: {mode_name} (RSVPs before {cutoff_time.strftime('%Y-%m-%d %I:%M %p UTC')})")
                return cutoff_time
        else:
            print("Please enter a number between 1 and 5")


def display_events(events):
    """
    Display a numbered list of events for user selection.

    Args:
        events: List of event tuples from database
    """
    print("\n" + "="*80)
    print("RECENT EVENTS")
    print("="*80)

    for idx, (db_id, name, luma_id, start_time) in enumerate(events, 1):
        date_str = start_time.strftime('%Y-%m-%d %I:%M %p') if start_time else 'Unknown date'
        print(f"{idx}. {name}")
        print(f"   Date: {date_str}")
        print(f"   Luma ID: {luma_id}")
        print()


def process_rsvps(event_api_id, cutoff_time=None):
    """
    Process all pending RSVPs for an event.

    Args:
        event_api_id: Luma event API identifier
        cutoff_time: Optional datetime object. Only auto-approve RSVPs before this time.
    """
    pending_guests = fetch_pending_rsvps(event_api_id)

    if not pending_guests:
        print("\nNo pending RSVPs found for this event.")
        return

    auto_approved = 0
    manually_approved = 0
    declined = 0
    skipped = 0

    print("\n" + "="*80)
    print("PROCESSING PENDING RSVPs")
    print("="*80 + "\n")

    for guest_entry in pending_guests:
        guest_data = guest_entry.get('guest', {})

        first_name = guest_data.get('user_first_name', '')
        last_name = guest_data.get('user_last_name', '')
        main_email = guest_data.get('email', '')

        # Parse RSVP timestamp
        rsvp_time = None
        created_at_str = guest_entry.get('created_at')
        if created_at_str:
            try:
                # Parse ISO 8601 format (e.g., "2024-01-15T10:30:00Z")
                rsvp_time = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
                # Convert to UTC naive datetime for comparison
                rsvp_time = rsvp_time.replace(tzinfo=None)
            except (ValueError, AttributeError):
                pass

        # Check if email qualifies for auto-approval
        is_approved, matching_email = check_approved_email(guest_data)

        if is_approved:
            # Check time filter for auto-approval
            if cutoff_time is not None and rsvp_time is not None:
                if rsvp_time > cutoff_time:
                    # Too recent - skip
                    print(f"⊘ SKIPPED (too recent): {first_name} {last_name} ({matching_email})")
                    skipped += 1
                    continue

            # Auto-approve (either no time filter or RSVP is old enough)
            try:
                approve_guest(event_api_id, main_email)
                print(f"✓ AUTO-APPROVED: {first_name} {last_name} ({matching_email})")
                auto_approved += 1
            except Exception as e:
                print(f"✗ Error approving {first_name} {last_name}: {e}")
        else:
            # No approved email - always prompt for manual decision (ignore time filter)
            school_email = get_registration_answer(guest_data, 'School email (.edu)')

            print(f"\n{'─'*80}")
            print(f"Name: {first_name} {last_name}")
            print(f"Email: {main_email}")
            if school_email:
                print(f"School Email: {school_email}")
            if rsvp_time:
                print(f"RSVP Time: {rsvp_time.strftime('%Y-%m-%d %I:%M %p UTC')}")

            # Prompt to decline
            while True:
                response = input(f"\nDecline this RSVP? (y/n): ").lower().strip()
                if response in ['y', 'n']:
                    break
                print("Please enter 'y' or 'n'")

            try:
                if response == 'y':
                    # Decline
                    decline_guest(event_api_id, main_email)
                    print(f"✗ DECLINED: {first_name} {last_name}")
                    declined += 1
                else:
                    # Approve
                    approve_guest(event_api_id, main_email)
                    print(f"✓ APPROVED: {first_name} {last_name}")
                    manually_approved += 1
            except Exception as e:
                print(f"✗ Error processing {first_name} {last_name}: {e}")

    # Display summary
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)
    print(f"Auto-approved (Harvard/MIT, old enough): {auto_approved}")
    print(f"Skipped (Harvard/MIT, too recent): {skipped}")
    print(f"Manually approved: {manually_approved}")
    print(f"Declined: {declined}")
    print(f"Total processed: {auto_approved + manually_approved + declined}")
    print()


def main():
    """Main function to run the RSVP approval script."""
    print("\n" + "="*80)
    print("LUMA EVENT RSVP APPROVAL TOOL")
    print("="*80)

    # Get time filter mode
    cutoff_time = get_time_filter_mode()

    # Fetch recent events
    events = fetch_recent_events(limit=10)

    if not events:
        print("\nNo events found in database.")
        return

    # Display events
    display_events(events)

    # Get user selection
    while True:
        try:
            selection = input(f"Select an event (1-{len(events)}) or 'q' to quit: ").strip()

            if selection.lower() == 'q':
                print("Exiting...")
                return

            event_idx = int(selection) - 1

            if 0 <= event_idx < len(events):
                break
            else:
                print(f"Please enter a number between 1 and {len(events)}")
        except ValueError:
            print("Please enter a valid number or 'q' to quit")

    # Get selected event details
    db_id, event_name, luma_event_id, start_time = events[event_idx]

    print(f"\nSelected: {event_name}")
    print(f"Processing pending RSVPs...")

    # Process RSVPs for selected event
    process_rsvps(luma_event_id, cutoff_time)


if __name__ == "__main__":
    main()

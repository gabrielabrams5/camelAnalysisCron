#!/usr/bin/env python3
"""
Auto-approve RSVPs for upcoming Luma events

Rules:
1. Scans events happening in the next 2 weeks
2. For each event, checks all pending (unapproved) RSVPs
3. Matches RSVPs to database by email (primary) or exact first+last name (backup)
4. Auto-approves if:
   - Person has attended 2+ events, OR
   - Event is within 24 hours AND person has Harvard/MIT email

Usage:
    python3 luma/auto_approve_rsvps.py              # Execute approvals
    python3 luma/auto_approve_rsvps.py --dry-run    # Preview without approving
    python3 luma/auto_approve_rsvps.py --verbose    # Detailed logging
"""

import os
import sys
import logging
import argparse
import psycopg2
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

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
LUMA_CALENDAR_ID = os.getenv('LUMA_CALENDAR_ID')
LUMA_API_BASE_URL = 'https://public-api.luma.com/v1'

# Approved email domains
APPROVED_DOMAINS = ['@college.harvard.edu', '@mit.edu', '@harvard.edu']

# API headers
HEADERS = {
    'Authorization': f'Bearer {LUMA_API_KEY}',
    'accept': 'application/json',
    'content-type': 'application/json'
}


def get_db_connection():
    """Establish database connection"""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        return conn
    except Exception as e:
        logging.error(f"Failed to connect to database: {e}")
        sys.exit(1)


def parse_luma_datetime(datetime_str, timezone_str):
    """
    Parse Luma datetime string to datetime object with timezone

    Args:
        datetime_str: ISO format datetime (e.g., "2024-02-01T18:00:00Z")
        timezone_str: Timezone name (e.g., "America/New_York")

    Returns:
        datetime object with timezone, or None if parsing fails
    """
    try:
        # Parse ISO format
        dt = datetime.fromisoformat(datetime_str.replace('Z', '+00:00'))

        # Convert to event timezone if specified
        if timezone_str:
            try:
                tz = ZoneInfo(timezone_str)
                dt = dt.astimezone(tz)
            except Exception:
                logging.warning(f"Unknown timezone: {timezone_str}, using UTC")

        return dt
    except Exception as e:
        logging.error(f"Failed to parse datetime {datetime_str}: {e}")
        return None


def get_luma_events():
    """
    Fetch all events from Luma API

    Returns:
        List of event dictionaries
    """
    url = f'{LUMA_API_BASE_URL}/calendar/list-events'
    params = {'calendar_api_id': LUMA_CALENDAR_ID}

    try:
        response = requests.get(url, headers=HEADERS, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        events = []
        for entry in data.get('entries', []):
            event = entry.get('event', {})
            event['api_id'] = entry.get('api_id')
            events.append(event)

        logging.info(f"Fetched {len(events)} events from Luma")
        return events

    except Exception as e:
        logging.error(f"Failed to fetch Luma events: {e}")
        return []


def filter_upcoming_events(events, weeks=2):
    """
    Filter events happening in the next N weeks

    Args:
        events: List of event dictionaries
        weeks: Number of weeks to look ahead (default: 2)

    Returns:
        List of upcoming events with parsed datetime
    """
    now = datetime.now(ZoneInfo('UTC'))
    cutoff = now + timedelta(weeks=weeks)

    upcoming = []
    for event in events:
        start_str = event.get('start_at')
        timezone_str = event.get('timezone', 'America/New_York')

        if not start_str:
            continue

        start_dt = parse_luma_datetime(start_str, timezone_str)
        if not start_dt:
            continue

        # Convert to UTC for comparison
        start_utc = start_dt.astimezone(ZoneInfo('UTC'))

        if now < start_utc < cutoff:
            event['start_datetime'] = start_dt
            upcoming.append(event)

    logging.info(f"Found {len(upcoming)} events in next {weeks} weeks")
    return upcoming


def fetch_pending_rsvps(event_api_id):
    """
    Fetch all pending RSVPs for a specific event (with pagination)

    Args:
        event_api_id: Luma event API ID

    Returns:
        List of guest entry dictionaries
    """
    url = f'{LUMA_API_BASE_URL}/event/get-guests'
    all_guests = []
    next_cursor = None

    while True:
        params = {
            'event_api_id': event_api_id,
            'approval_status': 'pending_approval'
        }
        if next_cursor:
            params['pagination_cursor'] = next_cursor

        try:
            response = requests.get(url, headers=HEADERS, params=params, timeout=60)
            response.raise_for_status()
            data = response.json()

            entries = data.get('entries', [])
            all_guests.extend(entries)

            # Handle pagination
            if not data.get('has_more', False):
                break
            next_cursor = data.get('next_cursor')
            if not next_cursor:
                break

        except Exception as e:
            logging.error(f"Failed to fetch pending RSVPs for event {event_api_id}: {e}")
            break

    return all_guests


def get_registration_answer(guest_data, question_label):
    """
    Extract answer from registration_answers array by question label

    Args:
        guest_data: Guest dictionary from Luma API
        question_label: Question label to search for (case-insensitive)

    Returns:
        Answer value string, or None if not found
    """
    registration_answers = guest_data.get('registration_answers', [])

    for answer in registration_answers:
        answer_label = answer.get('label', '')
        if answer_label.lower() == question_label.lower():
            return answer.get('value')

    return None


def check_approved_email(email_str):
    """
    Check if email is from an approved domain

    Args:
        email_str: Email address to check

    Returns:
        True if approved domain, False otherwise
    """
    if not email_str:
        return False

    email_lower = email_str.lower().strip()
    for domain in APPROVED_DOMAINS:
        if domain in email_lower:
            return True

    return False


def get_harvard_mit_email(guest_data):
    """
    Get Harvard/MIT email from guest data (checks main email and school email field)

    Args:
        guest_data: Guest dictionary from Luma API

    Returns:
        (has_approved_email: bool, email: str or None)
    """
    # Check main email
    main_email = guest_data.get('email') or ''
    if check_approved_email(main_email):
        return (True, main_email)

    # Check "School email (.edu)" custom field
    school_email = get_registration_answer(guest_data, 'School email (.edu)')
    if school_email and check_approved_email(school_email):
        return (True, school_email)

    return (False, None)


def find_person_in_db(cursor, guest_data):
    """
    Find person in database by email or exact name match

    Matching strategy (priority order):
    1. Match by main email
    2. Match by school email from registration_answers
    3. Fallback: exact first+last name match

    Args:
        cursor: Database cursor
        guest_data: Guest dictionary from Luma API

    Returns:
        Person dictionary with id, attendance_count, and emails, or None
    """
    email = (guest_data.get('email') or '').lower().strip()
    first_name = (guest_data.get('user_first_name') or '').strip()
    last_name = (guest_data.get('user_last_name') or '').strip()
    school_email = get_registration_answer(guest_data, 'School email (.edu)')

    # Try matching by main email
    if email:
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
                'attendance_count': result[5] or 0,
                'matched_by': 'email'
            }

    # Try matching by school email from registration
    if school_email:
        school_email_lower = school_email.lower().strip()
        cursor.execute("""
            SELECT id, first_name, last_name, school_email, personal_email,
                   event_attendance_count
            FROM people
            WHERE LOWER(school_email) = %s OR LOWER(personal_email) = %s
        """, (school_email_lower, school_email_lower))

        result = cursor.fetchone()
        if result:
            return {
                'id': result[0],
                'first_name': result[1],
                'last_name': result[2],
                'school_email': result[3],
                'personal_email': result[4],
                'attendance_count': result[5] or 0,
                'matched_by': 'school_email'
            }

    # Fallback: exact first+last name match
    if first_name and last_name:
        cursor.execute("""
            SELECT id, first_name, last_name, school_email, personal_email,
                   event_attendance_count
            FROM people
            WHERE LOWER(first_name) = %s AND LOWER(last_name) = %s
        """, (first_name.lower(), last_name.lower()))

        result = cursor.fetchone()
        if result:
            return {
                'id': result[0],
                'first_name': result[1],
                'last_name': result[2],
                'school_email': result[3],
                'personal_email': result[4],
                'attendance_count': result[5] or 0,
                'matched_by': 'name'
            }

    return None


def should_approve_rsvp(person, guest_data, event_start_datetime):
    """
    Determine if RSVP should be auto-approved

    Rules:
    - Approve if person has attended 2+ events, OR
    - Approve if event starts in ≤24 hours AND person has Harvard/MIT email

    Args:
        person: Person dictionary from database (or None)
        guest_data: Guest dictionary from Luma API
        event_start_datetime: Event start datetime object

    Returns:
        (should_approve: bool, reason: str)
    """
    # Rule 1: Person has attended 2+ events
    if person and person.get('attendance_count', 0) >= 2:
        return (True, f"returning attendee ({person['attendance_count']} events)")

    # Rule 2: Event is within 24 hours AND has Harvard/MIT email
    now = datetime.now(event_start_datetime.tzinfo)
    hours_until_event = (event_start_datetime - now).total_seconds() / 3600

    if hours_until_event <= 24:
        # Check if person has Harvard/MIT email from Luma data
        has_approved_email, approved_email = get_harvard_mit_email(guest_data)
        if has_approved_email:
            return (True, f"event in {hours_until_event:.1f}h + Harvard/MIT email")

        # Check if person in DB has Harvard/MIT email
        if person:
            if check_approved_email(person.get('school_email')):
                return (True, f"event in {hours_until_event:.1f}h + Harvard/MIT email (DB)")
            if check_approved_email(person.get('personal_email')):
                return (True, f"event in {hours_until_event:.1f}h + Harvard/MIT email (DB)")

    return (False, "does not meet criteria")


def approve_guest(event_api_id, guest_email, dry_run=False):
    """
    Approve a guest via Luma API

    Args:
        event_api_id: Luma event API ID
        guest_email: Guest email address
        dry_run: If True, don't actually make the API call

    Returns:
        True if successful, False otherwise
    """
    if dry_run:
        logging.info(f"[DRY RUN] Would approve: {guest_email}")
        return True

    url = f"{LUMA_API_BASE_URL}/event/update-guest-status"

    payload = {
        "guest": {
            "type": "email",
            "email": guest_email
        },
        "status": "approved",
        "event_api_id": event_api_id
    }

    try:
        response = requests.post(url, headers=HEADERS, json=payload, timeout=60)
        response.raise_for_status()
        logging.info(f"Approved: {guest_email}")
        return True
    except Exception as e:
        logging.error(f"Failed to approve {guest_email}: {e}")
        if hasattr(e, 'response') and hasattr(e.response, 'text'):
            logging.error(f"Response: {e.response.text}")
        return False


def process_event(conn, event, dry_run=False):
    """
    Process pending RSVPs for a single event

    Args:
        conn: Database connection
        event: Event dictionary with 'api_id', 'name', and 'start_datetime'
        dry_run: If True, don't actually approve RSVPs

    Returns:
        Dictionary with counts: approved, skipped, errors
    """
    event_api_id = event['api_id']
    event_name = event.get('name', 'Unnamed Event')
    event_start = event['start_datetime']

    logging.info(f"\n{'='*60}")
    logging.info(f"Processing: {event_name}")
    logging.info(f"Start time: {event_start.strftime('%Y-%m-%d %I:%M %p %Z')}")
    logging.info(f"Event ID: {event_api_id}")

    # Fetch pending RSVPs
    pending_guests = fetch_pending_rsvps(event_api_id)
    if not pending_guests:
        logging.info("No pending RSVPs")
        return {'approved': 0, 'skipped': 0, 'errors': 0}

    logging.info(f"Found {len(pending_guests)} pending RSVPs")

    cursor = conn.cursor()
    stats = {'approved': 0, 'skipped': 0, 'errors': 0}

    for guest_entry in pending_guests:
        guest_data = guest_entry.get('guest', {})
        email = guest_data.get('email') or 'unknown'
        first_name = guest_data.get('user_first_name') or ''
        last_name = guest_data.get('user_last_name') or ''
        name = f"{first_name} {last_name}".strip()

        # Find person in database
        person = find_person_in_db(cursor, guest_data)

        # Determine if should approve
        should_approve, reason = should_approve_rsvp(person, guest_data, event_start)

        if should_approve:
            # Log approval details
            if person:
                logging.debug(f"  ✓ {name} ({email}) - {reason} [matched by {person['matched_by']}]")
            else:
                logging.debug(f"  ✓ {name} ({email}) - {reason} [not in DB]")

            # Approve the RSVP
            if approve_guest(event_api_id, email, dry_run):
                stats['approved'] += 1
            else:
                stats['errors'] += 1
        else:
            logging.debug(f"  ✗ {name} ({email}) - {reason}")
            stats['skipped'] += 1

    cursor.close()

    # Summary for this event
    logging.info(f"Event summary: {stats['approved']} approved, {stats['skipped']} skipped, {stats['errors']} errors")

    return stats


def main():
    """Main execution function"""
    # Parse arguments
    parser = argparse.ArgumentParser(
        description='Auto-approve RSVPs for upcoming Luma events'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview approvals without executing them'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable detailed logging'
    )
    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    # Validate environment
    if not all([LUMA_API_KEY, LUMA_CALENDAR_ID]):
        logging.error("Missing required environment variables: LUMA_API_KEY, LUMA_CALENDAR_ID")
        sys.exit(1)

    logging.info("Starting Luma RSVP auto-approval script")
    if args.dry_run:
        logging.info("DRY RUN MODE - No approvals will be executed")

    # Connect to database
    conn = get_db_connection()

    try:
        # Fetch and filter events
        all_events = get_luma_events()
        upcoming_events = filter_upcoming_events(all_events, weeks=2)

        if not upcoming_events:
            logging.info("No upcoming events in next 2 weeks")
            return

        # Process each event
        total_stats = {'approved': 0, 'skipped': 0, 'errors': 0}

        for event in upcoming_events:
            stats = process_event(conn, event, dry_run=args.dry_run)
            total_stats['approved'] += stats['approved']
            total_stats['skipped'] += stats['skipped']
            total_stats['errors'] += stats['errors']

        # Final summary
        logging.info(f"\n{'='*60}")
        logging.info("FINAL SUMMARY")
        logging.info(f"Events processed: {len(upcoming_events)}")
        logging.info(f"Total approved: {total_stats['approved']}")
        logging.info(f"Total skipped: {total_stats['skipped']}")
        logging.info(f"Total errors: {total_stats['errors']}")

        if args.dry_run:
            logging.info("\nThis was a DRY RUN - no approvals were actually executed")

    finally:
        conn.close()
        logging.info("Database connection closed")


if __name__ == '__main__':
    main()

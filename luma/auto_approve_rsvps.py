#!/usr/bin/env python3
"""
Auto-approve RSVPs for upcoming Luma events

Rules:
1. Scans events happening in the next 2 weeks
2. For each event, checks all pending (unapproved) RSVPs
3. Matches RSVPs to database by email (primary) or exact first+last name (backup)
4. Auto-approves if:
   - Person has attended 2+ events, OR
   - Event is within 24 hours AND person has a VERIFIED student email:
       * an @mit.edu email, or
       * a Harvard email (@college.harvard.edu / @harvard.edu) that is ALSO
         present in the `harvard_students` table. That table is loaded from
         mailChimp/harvard_students_with_emails.csv by
         mailChimp/load_harvard_students.py. A Harvard-looking email that is
         not in the list is NOT approved (fail closed).

PAUSED: approvals are currently switched off. Without
LUMA_AUTO_APPROVE_ENABLED=true in the environment this script logs that it is
paused and exits 0 without approving anything.

Usage:
    python3 luma/auto_approve_rsvps.py              # Execute approvals
    python3 luma/auto_approve_rsvps.py --dry-run    # Preview without approving
    python3 luma/auto_approve_rsvps.py --verbose    # Detailed logging

Exit codes:
    0  ran to completion, every approval call succeeded
    1  configuration/API/DB failure, or at least one approval call failed
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

# Load environment variables (repo-root .env when run locally; no-op on Railway)
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

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

# Pause switch. Auto-approval is currently PAUSED: the cron runs this script
# (pipeline Step 2 and the hourly auto_approve service) but it approves nothing
# and exits 0. To resume without a code change, set
# LUMA_AUTO_APPROVE_ENABLED=true in the Railway service Variables tab.
# --dry-run still works while paused, since it never calls the approval API.
AUTO_APPROVE_ENABLED = (os.getenv('LUMA_AUTO_APPROVE_ENABLED') or '').strip().lower() \
    in ('1', 'true', 'yes', 'on')

# Approval thresholds
MIN_ATTENDANCE_FOR_APPROVAL = 2   # returning attendee rule
LAST_MINUTE_HOURS = 24            # verified-student rule window
LOOKAHEAD_WEEKS = 2               # how far ahead to scan for events

# School email domains. Harvard domains additionally require the address to be
# in the harvard_students table; MIT domains are trusted on the domain alone.
HARVARD_DOMAINS = ('@college.harvard.edu', '@harvard.edu')
MIT_DOMAINS = ('@mit.edu',)
APPROVED_DOMAINS = HARVARD_DOMAINS + MIT_DOMAINS

# API headers
HEADERS = {
    'x-luma-api-key': LUMA_API_KEY,
    'accept': 'application/json',
    'content-type': 'application/json'
}

# One keep-alive session for every Luma call: a single DNS lookup and TLS
# handshake per run instead of one per request (a flaky resolver otherwise
# adds its stall to every one of the dozens of calls a run makes).
SESSION = requests.Session()
SESSION.headers.update(HEADERS)


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
    Fetch all events from the Luma calendar, following pagination.

    Returns:
        List of event dictionaries, or None if the API call failed
    """
    url = f'{LUMA_API_BASE_URL}/calendar/list-events'
    events = []
    next_cursor = None
    page = 1

    while True:
        params = {'calendar_api_id': LUMA_CALENDAR_ID}
        if next_cursor:
            params['pagination_cursor'] = next_cursor

        try:
            response = SESSION.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            logging.error(f"Failed to fetch Luma events (page {page}): {e}")
            return None

        for entry in data.get('entries', []):
            event = entry.get('event', {})
            event['api_id'] = entry.get('api_id')
            events.append(event)

        if not data.get('has_more', False):
            break
        next_cursor = data.get('next_cursor')
        if not next_cursor:
            logging.warning("Luma reported has_more=true without next_cursor; stopping pagination")
            break
        page += 1

    logging.info(f"Fetched {len(events)} events from Luma ({page} page{'s' if page != 1 else ''})")
    return events


def filter_upcoming_events(events, weeks=LOOKAHEAD_WEEKS):
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
            response = SESSION.get(url, params=params, timeout=60)
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


def load_harvard_student_emails(cursor):
    """
    Load the verified Harvard student list from the harvard_students table.

    The table is populated from mailChimp/harvard_students_with_emails.csv by
    mailChimp/load_harvard_students.py. If the table is missing or empty this
    returns an empty set, which disables Harvard-email approvals (fail closed)
    and logs an error so the gap is visible in the pipeline output.

    Returns:
        set of lowercased email strings
    """
    try:
        cursor.execute("""
            SELECT LOWER(TRIM(email)), MAX(loaded_at) OVER ()
            FROM harvard_students
            WHERE email IS NOT NULL
        """)
        rows = cursor.fetchall()
    except psycopg2.Error as e:
        # A failed statement aborts the transaction; roll back so later queries work
        cursor.connection.rollback()
        logging.error(f"Could not read harvard_students table: {e}")
        rows = []

    emails = {row[0] for row in rows}
    if not emails:
        logging.error(
            "Harvard student list is EMPTY - Harvard-email approvals are disabled until "
            "mailChimp/load_harvard_students.py has been run against this database"
        )
    else:
        loaded_at = rows[0][1]
        logging.info(f"Loaded {len(emails)} verified Harvard student emails (list loaded {loaded_at:%Y-%m-%d})")
    return emails


def classify_email(email_str, harvard_emails):
    """
    Decide whether an email counts as a verified Harvard/MIT student email.

    Args:
        email_str: Email address to check
        harvard_emails: set of lowercased emails from the harvard_students table

    Returns:
        (verified: bool, label: str)
    """
    if not email_str:
        return (False, 'no email')

    email = email_str.lower().strip()

    if any(email.endswith(domain) for domain in MIT_DOMAINS):
        return (True, 'MIT email')

    if any(email.endswith(domain) for domain in HARVARD_DOMAINS):
        if email in harvard_emails:
            return (True, 'Harvard email verified in student list')
        return (False, 'Harvard email NOT in student list')

    return (False, 'not a Harvard/MIT email')


def check_approved_email(email_str, harvard_emails):
    """Backwards-compatible boolean wrapper around classify_email."""
    return classify_email(email_str, harvard_emails)[0]


def find_verified_school_email(guest_data, person, harvard_emails):
    """
    Look for a verified Harvard/MIT student email across every source we have:
    the RSVP's main email, the "School email (.edu)" registration answer, and
    the matched person's emails in the database.

    Returns:
        (verified: bool, email: str or None, label: str or None, checks: list[str])
        `checks` describes every email examined, for logging.
    """
    candidates = [
        ('main', guest_data.get('email')),
        ('school', get_registration_answer(guest_data, 'School email (.edu)')),
    ]
    if person:
        candidates.append(('DB school', person.get('school_email')))
        candidates.append(('DB personal', person.get('personal_email')))

    checks = []
    seen = set()
    for source, email in candidates:
        if not email:
            continue
        normalized = email.lower().strip()
        if normalized in seen:
            continue
        seen.add(normalized)

        verified, label = classify_email(email, harvard_emails)
        checks.append(f"{source}: {normalized} ({label})")
        if verified:
            return (True, normalized, label, checks)

    return (False, None, None, checks)


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


def should_approve_rsvp(person, guest_data, event_start_datetime, harvard_emails):
    """
    Determine if RSVP should be auto-approved

    Rules:
    - Approve if person has attended 2+ events, OR
    - Approve if event starts in ≤24 hours AND person has a verified student
      email (MIT domain, or Harvard domain present in the harvard_students list)

    Args:
        person: Person dictionary from database (or None)
        guest_data: Guest dictionary from Luma API
        event_start_datetime: Event start datetime object
        harvard_emails: set of lowercased verified Harvard student emails

    Returns:
        (should_approve: bool, reason: str)
    """
    # Rule 1: Person has attended enough events
    if person and person.get('attendance_count', 0) >= MIN_ATTENDANCE_FOR_APPROVAL:
        return (True, f"returning attendee ({person['attendance_count']} events)")

    # Rule 2: Event is within the last-minute window AND verified student email
    now = datetime.now(event_start_datetime.tzinfo)
    hours_until_event = (event_start_datetime - now).total_seconds() / 3600

    verified, verified_email, label, checks = find_verified_school_email(
        guest_data, person, harvard_emails
    )

    if hours_until_event <= LAST_MINUTE_HOURS and verified:
        return (True, f"event in {hours_until_event:.1f}h + {label} ({verified_email})")

    # Build detailed rejection message
    rejection_parts = []

    if person:
        rejection_parts.append(f"found in DB (matched by {person.get('matched_by', 'unknown')})")
    else:
        rejection_parts.append("not in DB")

    attendance_count = person.get('attendance_count', 0) if person else 0
    rejection_parts.append(
        f"{attendance_count} event{'s' if attendance_count != 1 else ''} "
        f"(needs {MIN_ATTENDANCE_FOR_APPROVAL}+)"
    )

    rejection_parts.append(
        f"event in {hours_until_event:.1f}h (needs ≤{LAST_MINUTE_HOURS}h for verified-student approval)"
    )

    if checks:
        rejection_parts.append("checked emails: " + ", ".join(checks))
    else:
        rejection_parts.append("no emails to check")

    return (False, " | ".join(rejection_parts))


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
        response = SESSION.post(url, json=payload, timeout=60)
        response.raise_for_status()
        logging.info(f"Approved: {guest_email}")
        return True
    except Exception as e:
        logging.error(f"Failed to approve {guest_email}: {e}")
        response = getattr(e, 'response', None)
        if response is not None and getattr(response, 'text', None):
            logging.error(f"Response: {response.text}")
        return False


def process_event(conn, event, harvard_emails, dry_run=False):
    """
    Process pending RSVPs for a single event

    Args:
        conn: Database connection
        event: Event dictionary with 'api_id', 'name', and 'start_datetime'
        harvard_emails: set of lowercased verified Harvard student emails
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
        should_approve, reason = should_approve_rsvp(person, guest_data, event_start, harvard_emails)

        # Decisions are logged at INFO so the cron output shows WHY each RSVP
        # was or wasn't approved without needing --verbose.
        if should_approve:
            matched = f"matched by {person['matched_by']}" if person else "not in DB"
            logging.info(f"  ✓ {name} ({email}) - {reason} [{matched}]")

            # Approve the RSVP
            if approve_guest(event_api_id, email, dry_run):
                stats['approved'] += 1
            else:
                stats['errors'] += 1
        else:
            logging.info(f"  ✗ {name} ({email}) - {reason}")
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

    # Paused? Do nothing and exit successfully, so the pipeline step doesn't
    # get recorded as a failure. --dry-run is still allowed (it approves nothing).
    if not AUTO_APPROVE_ENABLED and not args.dry_run:
        logging.info("RSVP auto-approval is PAUSED - no RSVPs will be approved.")
        logging.info("Set LUMA_AUTO_APPROVE_ENABLED=true to resume.")
        sys.exit(0)

    # Validate environment
    if not all([LUMA_API_KEY, LUMA_CALENDAR_ID]):
        logging.error("Missing required environment variables: LUMA_API_KEY, LUMA_CALENDAR_ID")
        sys.exit(1)

    logging.info("Starting Luma RSVP auto-approval script")
    if args.dry_run:
        logging.info("DRY RUN MODE - No approvals will be executed")

    # Connect to database
    conn = get_db_connection()
    exit_code = 0

    try:
        # Load the verified Harvard student list once
        cursor = conn.cursor()
        harvard_emails = load_harvard_student_emails(cursor)
        cursor.close()

        # Fetch and filter events
        all_events = get_luma_events()
        if all_events is None:
            logging.error("Could not fetch events from Luma - aborting")
            sys.exit(1)

        upcoming_events = filter_upcoming_events(all_events, weeks=LOOKAHEAD_WEEKS)

        if not upcoming_events:
            logging.info(f"No upcoming events in next {LOOKAHEAD_WEEKS} weeks")
            return

        # Process each event
        total_stats = {'approved': 0, 'skipped': 0, 'errors': 0}

        for event in upcoming_events:
            stats = process_event(conn, event, harvard_emails, dry_run=args.dry_run)
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

        if total_stats['errors'] > 0:
            exit_code = 1

    finally:
        conn.close()
        logging.info("Database connection closed")

    sys.exit(exit_code)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
Standalone script to tag event attendees and RSVP no-shows in Mailchimp.

Usage:
    # Tag both attendees and RSVP no-shows (default)
    python mailChimp/tag_mailchimp_attendees.py --event-id 123

    # Tag only attendees (skip RSVP no-shows)
    python mailChimp/tag_mailchimp_attendees.py --event-id 123 --only-attendees

This script:
1. Queries the Railway PostgreSQL database for:
   - Checked-in attendees (checked_in = TRUE)
   - RSVP no-shows (checked_in = FALSE) - unless --only-attendees is set
2. Tags attendees in Mailchimp with "{sanitized_event_name}_attended"
3. Tags RSVP no-shows in Mailchimp with "{sanitized_event_name}_rsvp_no_show"
"""

import os
import sys
import argparse
import logging
from typing import List, Dict, Optional
from dotenv import load_dotenv
import psycopg2

# Import our Mailchimp client module (relative import since we're in the same directory)
from mailchimp_client import batch_tag_attendees, sanitize_event_name

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)


def get_db_connection():
    """
    Establish connection to Railway PostgreSQL database using environment variables.

    Returns:
        psycopg2 connection object

    Raises:
        ConnectionError: If connection fails or required env vars are missing
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
    except Exception as e:
        raise ConnectionError(f"Database connection error: {e}")


def get_event_attendees(event_id: int) -> tuple[Optional[str], List[Dict[str, str]]]:
    """
    Query database for all checked-in attendees of a specific event.

    Deduplicates by email address to prevent Mailchimp batch errors.
    If multiple attendees share an email, keeps the most recent attendance record.

    Args:
        event_id: Database ID of the event

    Returns:
        Tuple of (event_name, attendees_list) where attendees_list is a list of dicts with:
            - email: Email address (unique per event)
            - first_name: First name
            - last_name: Last name

    Raises:
        ValueError: If event_id not found in database
        ConnectionError: If database query fails
    """
    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # First, verify the event exists and get its name
        cursor.execute("""
            SELECT event_name
            FROM events
            WHERE id = %s
        """, (event_id,))

        event_result = cursor.fetchone()
        if not event_result:
            raise ValueError(f"Event ID {event_id} not found in database")

        event_name = event_result[0]
        logging.info(f"Found event: '{event_name}' (ID: {event_id})")

        # Query for all checked-in attendees with contact information
        # DISTINCT ON ensures each email appears only once (prevents Mailchimp batch errors)
        # If multiple people share an email, keeps the most recent attendance record
        query = """
            SELECT DISTINCT ON (COALESCE(p.school_email, p.personal_email))
                p.first_name,
                p.last_name,
                COALESCE(p.school_email, p.personal_email) as email,
                a.is_first_event
            FROM attendance a
            JOIN people p ON a.person_id = p.id
            JOIN events e ON a.event_id = e.id
            WHERE a.event_id = %s
              AND a.checked_in = TRUE
            ORDER BY COALESCE(p.school_email, p.personal_email), a.id DESC
        """

        cursor.execute(query, (event_id,))
        rows = cursor.fetchall()

        attendees = []
        skipped_count = 0

        for row in rows:
            first_name, last_name, email, is_first_event = row

            # Skip if no email available
            if not email:
                logging.warning(
                    f"Skipping {first_name} {last_name} - no email address"
                )
                skipped_count += 1
                continue

            # Warn about obviously invalid emails (but still include them - Mailchimp will reject)
            if (email.endswith('.con') or  # Common typo: .con instead of .com
                '@' not in email or
                ' ' in email or
                email.startswith('what ') or  # Form field questions
                '-deleted' in email):  # Deleted email markers
                logging.warning(
                    f"Potentially invalid email for {first_name} {last_name}: {email}"
                )

            attendees.append({
                'email': email,
                'first_name': first_name,
                'last_name': last_name
            })

        logging.info(
            f"Found {len(attendees)} checked-in attendees with emails "
            f"({skipped_count} skipped due to missing email)"
        )

        return event_name, attendees

    except psycopg2.Error as e:
        logging.error(f"Database query error: {e}")
        raise ConnectionError(f"Failed to query attendees: {e}")

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def get_event_rsvp_no_shows(event_id: int) -> List[Dict[str, str]]:
    """
    Query database for all people who RSVP'd but did not check in to a specific event.

    Deduplicates by email address to prevent Mailchimp batch errors.
    If multiple RSVPs share an email, keeps the most recent attendance record.

    Args:
        event_id: Database ID of the event

    Returns:
        List of dicts with:
            - email: Email address (unique per event)
            - first_name: First name
            - last_name: Last name

    Raises:
        ConnectionError: If database query fails
    """
    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Query for all RSVPs who did NOT check in
        # DISTINCT ON ensures each email appears only once (prevents Mailchimp batch errors)
        # If multiple people share an email, keeps the most recent attendance record
        query = """
            SELECT DISTINCT ON (COALESCE(p.school_email, p.personal_email))
                p.first_name,
                p.last_name,
                COALESCE(p.school_email, p.personal_email) as email
            FROM attendance a
            JOIN people p ON a.person_id = p.id
            JOIN events e ON a.event_id = e.id
            WHERE a.event_id = %s
              AND a.checked_in = FALSE
            ORDER BY COALESCE(p.school_email, p.personal_email), a.id DESC
        """

        cursor.execute(query, (event_id,))
        rows = cursor.fetchall()

        rsvp_no_shows = []
        skipped_count = 0

        for row in rows:
            first_name, last_name, email = row

            # Skip if no email available
            if not email:
                logging.warning(
                    f"Skipping {first_name} {last_name} - no email address"
                )
                skipped_count += 1
                continue

            # Warn about obviously invalid emails (but still include them - Mailchimp will reject)
            if (email.endswith('.con') or  # Common typo: .con instead of .com
                '@' not in email or
                ' ' in email or
                email.startswith('what ') or  # Form field questions
                '-deleted' in email):  # Deleted email markers
                logging.warning(
                    f"Potentially invalid email for {first_name} {last_name}: {email}"
                )

            rsvp_no_shows.append({
                'email': email,
                'first_name': first_name,
                'last_name': last_name
            })

        logging.info(
            f"Found {len(rsvp_no_shows)} RSVP no-shows with emails "
            f"({skipped_count} skipped due to missing email)"
        )

        return rsvp_no_shows

    except psycopg2.Error as e:
        logging.error(f"Database query error: {e}")
        raise ConnectionError(f"Failed to query RSVP no-shows: {e}")

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def main():
    """
    Main execution function.

    Parses command-line arguments, queries database, and tags attendees in Mailchimp.
    """
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description='Tag event attendees and RSVP no-shows in Mailchimp based on Railway database records'
    )
    parser.add_argument(
        '--event-id',
        type=int,
        required=True,
        help='Database ID of the event to tag attendees for'
    )
    parser.add_argument(
        '--only-attendees',
        action='store_true',
        help='Only tag attendees (skip RSVP no-shows). By default, tags both groups.'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Query database but skip Mailchimp API calls (for testing)'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )

    args = parser.parse_args()

    # Update logging level if verbose
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Load environment variables
    load_dotenv()

    # Validate required environment variables
    required_env_vars = [
        'PGHOST', 'PGPORT', 'PGDATABASE', 'PGUSER', 'PGPASSWORD'
    ]
    if not args.dry_run:
        required_env_vars.extend([
            'MAILCHIMP_API_KEY',
            'MAILCHIMP_SERVER_PREFIX',
            'MAILCHIMP_AUDIENCE_ID'
        ])

    missing_vars = [var for var in required_env_vars if not os.getenv(var)]
    if missing_vars:
        logging.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        sys.exit(1)

    try:
        # Query database for attendees
        logging.info(f"Querying database for event ID: {args.event_id}")
        event_name, attendees = get_event_attendees(args.event_id)

        # Query database for RSVP no-shows (unless --only-attendees is set)
        rsvp_no_shows = []
        if not args.only_attendees:
            rsvp_no_shows = get_event_rsvp_no_shows(args.event_id)

        if not attendees and not rsvp_no_shows:
            logging.warning(f"No attendees or RSVPs found for event '{event_name}'")
            print(f"\nNo one to tag for event: {event_name}")
            sys.exit(0)

        # Display summary
        print(f"\nEvent: {event_name}")
        print(f"Event ID: {args.event_id}")
        print(f"\nGroups to tag:")
        print(f"  Checked-in attendees: {len(attendees)}")
        if attendees:
            print(f"    Tag: {sanitize_event_name(event_name)}_attended")
        if not args.only_attendees:
            print(f"  RSVP no-shows: {len(rsvp_no_shows)}")
            if rsvp_no_shows:
                print(f"    Tag: {sanitize_event_name(event_name)}_rsvp_no_show")

        if args.dry_run:
            print("\n[DRY RUN MODE - Skipping Mailchimp API calls]")

            if attendees:
                print("\nAttendees to be tagged with '_attended':")
                for i, attendee in enumerate(attendees[:10], 1):  # Show first 10
                    print(f"  {i}. {attendee['first_name']} {attendee['last_name']} <{attendee['email']}>")
                if len(attendees) > 10:
                    print(f"  ... and {len(attendees) - 10} more")

            if rsvp_no_shows:
                print("\nRSVP no-shows to be tagged with '_rsvp_no_show':")
                for i, person in enumerate(rsvp_no_shows[:10], 1):  # Show first 10
                    print(f"  {i}. {person['first_name']} {person['last_name']} <{person['email']}>")
                if len(rsvp_no_shows) > 10:
                    print(f"  ... and {len(rsvp_no_shows) - 10} more")

            sys.exit(0)

        # Tag attendees in Mailchimp
        logging.info("Starting Mailchimp tagging process...")

        # Initialize combined stats
        combined_stats = {
            'attendees': {'total': 0, 'upserted': 0, 'tagged': 0, 'errors': 0},
            'rsvp_no_shows': {'total': 0, 'upserted': 0, 'tagged': 0, 'errors': 0}
        }

        # Tag checked-in attendees
        if attendees:
            logging.info(f"Tagging {len(attendees)} attendees...")
            attendees_stats = batch_tag_attendees(
                attendees=attendees,
                event_name=event_name,
                tag_suffix="attended"
            )
            combined_stats['attendees'] = attendees_stats

        # Tag RSVP no-shows
        if rsvp_no_shows:
            logging.info(f"Tagging {len(rsvp_no_shows)} RSVP no-shows...")
            no_show_stats = batch_tag_attendees(
                attendees=rsvp_no_shows,
                event_name=event_name,
                tag_suffix="rsvp_no_show"
            )
            combined_stats['rsvp_no_shows'] = no_show_stats

        # Display results
        print("\n" + "="*60)
        print("MAILCHIMP TAGGING RESULTS")
        print("="*60)

        if attendees:
            print("\nAttendees (tagged with '_attended'):")
            print(f"  Total:               {combined_stats['attendees']['total']}")
            print(f"  Successfully upserted: {combined_stats['attendees']['upserted']}")
            print(f"  Successfully tagged:   {combined_stats['attendees']['tagged']}")
            print(f"  Errors:               {combined_stats['attendees']['errors']}")

        if rsvp_no_shows:
            print("\nRSVP No-Shows (tagged with '_rsvp_no_show'):")
            print(f"  Total:               {combined_stats['rsvp_no_shows']['total']}")
            print(f"  Successfully upserted: {combined_stats['rsvp_no_shows']['upserted']}")
            print(f"  Successfully tagged:   {combined_stats['rsvp_no_shows']['tagged']}")
            print(f"  Errors:               {combined_stats['rsvp_no_shows']['errors']}")

        total_errors = combined_stats['attendees']['errors'] + combined_stats['rsvp_no_shows']['errors']
        total_people = combined_stats['attendees']['total'] + combined_stats['rsvp_no_shows']['total']

        print("\nOverall Summary:")
        print(f"  Total people processed: {total_people}")
        print(f"  Total errors:          {total_errors}")
        print("="*60)

        # Exit with appropriate status code
        if total_errors > 0:
            logging.warning(f"Completed with {total_errors} errors")
            sys.exit(2)  # Partial success
        else:
            logging.info("Successfully tagged all people")
            sys.exit(0)  # Success

    except ValueError as e:
        logging.error(f"Invalid event ID: {e}")
        sys.exit(1)

    except ConnectionError as e:
        logging.error(f"Database connection error: {e}")
        sys.exit(1)

    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        import traceback
        logging.debug(traceback.format_exc())
        sys.exit(1)


if __name__ == '__main__':
    main()

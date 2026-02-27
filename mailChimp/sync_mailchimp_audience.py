#!/usr/bin/env python3
"""
Standalone script to sync entire mailing list from Railway database to Mailchimp.

Usage:
    python mailChimp/sync_mailchimp_audience.py

This script:
1. Queries the Railway PostgreSQL database for all people with emails
2. Deduplicates using COALESCE(school_email, personal_email) - prefers school email
3. Syncs all contacts to Mailchimp audience (creates new + updates existing)
4. Does NOT remove contacts from Mailchimp if they're removed from database
"""

import os
import sys
import argparse
import logging
from typing import List, Dict
from dotenv import load_dotenv
import psycopg2

# Import our Mailchimp client module (relative import since we're in the same directory)
from mailchimp_client import sync_full_audience

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


def get_all_contacts() -> List[Dict[str, str]]:
    """
    Query database for all people with email addresses.

    Uses COALESCE to prefer school_email over personal_email for deduplication.
    Each unique email address appears only once in the results (even if multiple
    people share the same email). If duplicates exist, keeps the most recent record.

    Returns:
        List of contact dictionaries with:
            - email: Email address (school_email preferred, unique)
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

        # Query all people with at least one email
        # COALESCE prioritizes school_email over personal_email
        # DISTINCT ON ensures each email appears only once (prevents Mailchimp batch errors)
        query = """
            SELECT DISTINCT ON (COALESCE(school_email, personal_email))
                first_name,
                last_name,
                COALESCE(school_email, personal_email) as email
            FROM people
            WHERE school_email IS NOT NULL OR personal_email IS NOT NULL
            ORDER BY COALESCE(school_email, personal_email), id DESC
        """

        cursor.execute(query)
        rows = cursor.fetchall()

        contacts = []
        skipped_count = 0

        for row in rows:
            first_name, last_name, email = row

            # Skip if no email (should not happen due to WHERE clause, but safety check)
            if not email:
                logging.warning(
                    f"Skipping {first_name} {last_name} - no email address"
                )
                skipped_count += 1
                continue

            contacts.append({
                'email': email,
                'first_name': first_name,
                'last_name': last_name
            })

        logging.info(
            f"Found {len(contacts)} contacts with emails "
            f"({skipped_count} skipped due to missing email)"
        )

        return contacts

    except psycopg2.Error as e:
        logging.error(f"Database query error: {e}")
        raise ConnectionError(f"Failed to query contacts: {e}")

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def main():
    """
    Main execution function.

    Parses command-line arguments, queries database, and syncs contacts to Mailchimp.
    """
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description='Sync entire mailing list from Railway database to Mailchimp audience'
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
    parser.add_argument(
        '--batch-size',
        type=int,
        default=500,
        help='Number of contacts to sync per batch (default: 500, max: 500)'
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
        # Query database for all contacts
        logging.info("Querying database for all contacts with emails...")
        contacts = get_all_contacts()

        if not contacts:
            logging.warning("No contacts found in database")
            print("\nNo contacts to sync")
            sys.exit(0)

        # Display summary
        print(f"\nMailing List Sync")
        print(f"=" * 60)
        print(f"Total contacts found: {len(contacts)}")
        print(f"Batch size: {args.batch_size}")

        if args.dry_run:
            print("\n[DRY RUN MODE - Skipping Mailchimp API calls]")
            print("\nFirst 10 contacts to be synced:")
            for i, contact in enumerate(contacts[:10], 1):
                print(f"  {i}. {contact['first_name']} {contact['last_name']} <{contact['email']}>")
            if len(contacts) > 10:
                print(f"  ... and {len(contacts) - 10} more")
            sys.exit(0)

        # Sync contacts to Mailchimp
        logging.info("Starting Mailchimp audience sync...")
        stats = sync_full_audience(
            contacts=contacts,
            batch_size=args.batch_size
        )

        # Display results
        print("\n" + "="*60)
        print("MAILCHIMP AUDIENCE SYNC RESULTS")
        print("="*60)
        print(f"Total contacts processed: {stats['total']}")
        print(f"New contacts added:       {stats['new']}")
        print(f"Existing contacts updated:{stats['updated']}")
        print(f"Errors:                   {stats['errors']}")
        print("="*60)

        # Calculate success rate
        success_count = stats['new'] + stats['updated']
        if stats['total'] > 0:
            success_rate = (success_count / stats['total']) * 100
            print(f"Success rate: {success_rate:.1f}%")

        # Exit with appropriate status code
        if stats['errors'] > 0:
            if success_count > 0:
                logging.warning(f"Completed with {stats['errors']} errors")
                sys.exit(2)  # Partial success
            else:
                logging.error("Sync failed - all contacts had errors")
                sys.exit(1)  # Complete failure
        else:
            logging.info("Successfully synced all contacts")
            sys.exit(0)  # Success

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

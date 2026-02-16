#!/usr/bin/env python3
"""
Luma Event Sync Script
Syncs event metadata from Luma API to Railway database
Downloads attendance CSVs for past events that haven't been analyzed
"""

import os
import sys
import json
import logging
import tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
import psycopg2
import requests

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
LUMA_CALENDAR_ID = os.getenv('LUMA_CALENDAR_ID', '')  # Optional, if needed for API
LUMA_API_BASE_URL = 'https://public-api.luma.com/v1'


def get_db_connection():
    """Create and return a database connection"""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        return conn
    except Exception as e:
        logging.error(f"Failed to connect to database: {e}")
        sys.exit(1)


def get_luma_events():
    """
    Fetch all events from Luma API
    Returns: List of event dictionaries
    """
    if not LUMA_API_KEY:
        logging.error("LUMA_API_KEY not found in environment variables")
        sys.exit(1)

    headers = {
        'x-luma-api-key': LUMA_API_KEY,
        'Content-Type': 'application/json'
    }

    try:
        # Luma API endpoint for listing calendar events
        url = f'{LUMA_API_BASE_URL}/calendar/list-events'

        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        data = response.json()

        # Luma API returns events in 'entries' array
        # Each entry has 'api_id' and 'event' object
        entries = data.get('entries', [])

        # Extract the event objects from entries
        events = []
        for entry in entries:
            event = entry.get('event', {})
            # Add the api_id to the event for reference
            event['api_id'] = entry.get('api_id')
            events.append(event)

        logging.info(f"Fetched {len(events)} events from Luma API")
        return events

    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to fetch events from Luma API: {e}")
        sys.exit(1)


def download_event_csv(event_api_id, save_path):
    """
    Download attendance CSV for a specific event from Luma
    Args:
        event_api_id: Luma event API ID
        save_path: Path to save the CSV file
    Returns: True if successful, False otherwise
    """
    headers = {
        'x-luma-api-key': LUMA_API_KEY,
    }

    try:
        # Luma API CSV export endpoint
        url = f'{LUMA_API_BASE_URL}/event/get-guests-csv'
        params = {'api_id': event_api_id}

        response = requests.get(url, headers=headers, params=params, timeout=60, stream=True)
        response.raise_for_status()

        with open(save_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        logging.info(f"Downloaded CSV for event {event_api_id} to {save_path}")
        return True

    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to download CSV for event {event_api_id}: {e}")
        return False


def event_exists_in_db(cursor, luma_event_id):
    """
    Check if an event already exists in the database
    Args:
        cursor: Database cursor
        luma_event_id: Luma event ID to check
    Returns: Tuple (exists: bool, db_event_id: int or None, attendance: int)
    """
    cursor.execute("""
        SELECT id, attendance
        FROM events
        WHERE luma_event_id = %s
    """, (luma_event_id,))

    result = cursor.fetchone()
    if result:
        return True, result[0], result[1] or 0
    return False, None, 0


def create_event(cursor, luma_event):
    """
    Create a new event in the database from Luma event data
    Args:
        cursor: Database cursor
        luma_event: Luma event dictionary
    Returns: Database event ID
    """
    # Extract and parse fields from Luma event
    # Luma API fields: name, start_at, description, url, api_id, geo_address_json, cover_url, timezone, etc.
    event_name = luma_event.get('name', 'Untitled Event')
    timezone_str = luma_event.get('timezone')  # Get timezone from Luma
    start_datetime = parse_luma_datetime(luma_event.get('start_at'), timezone_str)
    description = luma_event.get('description', '')
    signup_url = luma_event.get('url', '')
    cover_image_url = luma_event.get('cover_url', '')
    luma_event_id = luma_event.get('api_id')
    geo_location = luma_event.get('geo_address_json', {})
    location_str = geo_location.get('city', 'TBD') if isinstance(geo_location, dict) else 'TBD'

    cursor.execute("""
        INSERT INTO events (
            event_name,
            start_datetime,
            description,
            rsvp_link,
            speaker_headshot_url,
            luma_event_id,
            location,
            attendance
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, 0)
        RETURNING id
    """, (
        event_name[:100],  # Respect VARCHAR(100) limit
        start_datetime,
        description,
        signup_url,
        cover_image_url,
        luma_event_id,
        location_str[:100]  # Respect VARCHAR(100) limit
    ))

    event_id = cursor.fetchone()[0]
    logging.info(f"Created new event: {event_name} (DB ID: {event_id}, Luma ID: {luma_event_id})")
    return event_id


def update_event_if_missing(cursor, db_event_id, luma_event):
    """
    Update event fields that are NULL/missing in database
    Args:
        cursor: Database cursor
        db_event_id: Database event ID
        luma_event: Luma event dictionary
    """
    # Extract fields from Luma event
    event_name = luma_event.get('name', '')
    timezone_str = luma_event.get('timezone')  # Get timezone from Luma
    start_datetime = parse_luma_datetime(luma_event.get('start_at'), timezone_str)
    description = luma_event.get('description', '')
    signup_url = luma_event.get('url', '')
    cover_image_url = luma_event.get('cover_url', '')

    # Update fields - always update start_datetime to ensure correct timezone
    # Other fields only update if NULL using COALESCE pattern
    cursor.execute("""
        UPDATE events
        SET
            event_name = COALESCE(NULLIF(event_name, ''), %s, event_name),
            start_datetime = %s,
            description = COALESCE(description, %s),
            rsvp_link = COALESCE(rsvp_link, %s),
            speaker_headshot_url = COALESCE(speaker_headshot_url, %s)
        WHERE id = %s
    """, (
        event_name[:100],
        start_datetime,
        description,
        signup_url,
        cover_image_url,
        db_event_id
    ))

    if cursor.rowcount > 0:
        logging.info(f"Updated missing fields for event ID {db_event_id}")


def parse_luma_datetime(datetime_str, timezone_str=None):
    """
    Parse Luma datetime string to Python datetime, with timezone conversion

    Args:
        datetime_str: ISO format datetime string from Luma (in UTC)
        timezone_str: IANA timezone string (e.g., 'America/New_York')
                     If provided, converts UTC time to this timezone

    Returns: datetime object or None
    """
    if not datetime_str:
        return None

    try:
        # Parse ISO format datetime
        # Luma returns UTC times with 'Z' suffix
        if datetime_str.endswith('Z'):
            datetime_str = datetime_str[:-1]

        # Parse as UTC
        dt = datetime.fromisoformat(datetime_str)

        # Add UTC timezone info
        dt = dt.replace(tzinfo=ZoneInfo('UTC'))

        # Convert to event's local timezone if provided
        if timezone_str:
            try:
                local_tz = ZoneInfo(timezone_str)
                dt = dt.astimezone(local_tz)
            except Exception as e:
                logging.warning(f"Failed to convert to timezone {timezone_str}: {e}, using UTC")

        # Return as naive datetime for PostgreSQL TIMESTAMP column
        # (PostgreSQL will store it as-is without timezone)
        return dt.replace(tzinfo=None)

    except (ValueError, AttributeError) as e:
        logging.error(f"Failed to parse datetime: {datetime_str} - {e}")
        return None


def sync_events():
    """
    Main sync logic:
    1. Fetch events from Luma
    2. Create/update future events
    3. Download CSVs for past events that need processing
    Returns: List of events that need attendance import
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Fetch all events from Luma
        luma_events = get_luma_events()

        now = datetime.now()
        one_day_ago = now - timedelta(days=1)

        events_to_process = []

        for luma_event in luma_events:
            luma_event_id = luma_event.get('api_id')
            if not luma_event_id:
                logging.warning(f"Skipping event without ID: {luma_event.get('name', 'Unknown')}")
                continue

            # Parse event start time with timezone
            timezone_str = luma_event.get('timezone')
            start_datetime = parse_luma_datetime(luma_event.get('start_at'), timezone_str)
            if not start_datetime:
                logging.warning(f"Skipping event without valid start time: {luma_event.get('name', 'Unknown')}")
                continue

            # Check if event exists in database
            exists, db_event_id, attendance = event_exists_in_db(cursor, luma_event_id)

            if start_datetime > now:
                # Future event - create or update metadata
                if not exists:
                    create_event(cursor, luma_event)
                else:
                    update_event_if_missing(cursor, db_event_id, luma_event)

            elif start_datetime < one_day_ago:
                # Past event (>1 day old)
                if not exists:
                    # Create the event first
                    db_event_id = create_event(cursor, luma_event)
                    attendance = 0

                # Check if needs attendance processing
                if attendance == 0:
                    # Download CSV to temp file
                    temp_file = tempfile.NamedTemporaryFile(
                        mode='w+b',
                        suffix='.csv',
                        delete=False,
                        prefix=f'luma_event_{luma_event_id}_'
                    )
                    temp_path = temp_file.name
                    temp_file.close()

                    if download_event_csv(luma_event_id, temp_path):
                        events_to_process.append({
                            'event_id': db_event_id,
                            'csv_path': temp_path,
                            'luma_event_id': luma_event_id,
                            'event_name': luma_event.get('name', 'Unknown')
                        })
                    else:
                        # Clean up temp file if download failed
                        os.unlink(temp_path)

        # Commit all database changes
        conn.commit()
        logging.info(f"Sync complete. Found {len(events_to_process)} events needing attendance import.")

        return events_to_process

    except Exception as e:
        conn.rollback()
        logging.error(f"Error during sync: {e}")
        raise
    finally:
        cursor.close()
        conn.close()


def main():
    """Main entry point"""
    try:
        events_to_process = sync_events()

        if events_to_process:
            # Output JSON list for next stage of pipeline
            print(json.dumps(events_to_process, indent=2))
            sys.exit(0)  # Exit code 0 means there are events to process
        else:
            # No events to process
            logging.info("No events need attendance import at this time.")
            sys.exit(1)  # Exit code 1 means no events to process

    except Exception as e:
        logging.error(f"Fatal error: {e}")
        sys.exit(2)  # Exit code 2 means error occurred


if __name__ == '__main__':
    main()

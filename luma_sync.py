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

    if not LUMA_CALENDAR_ID:
        logging.error("LUMA_CALENDAR_ID not found in environment variables")
        sys.exit(1)

    headers = {
        'Authorization': f'Bearer {LUMA_API_KEY}',
        'Content-Type': 'application/json'
    }

    try:
        # Luma API endpoint for listing calendar events
        url = f'{LUMA_API_BASE_URL}/calendar/list-events'
        params = {
            'calendar_api_id': LUMA_CALENDAR_ID
        }

        response = requests.get(url, headers=headers, params=params, timeout=30)
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


def download_event_json(event_api_id, save_path):
    """
    Download attendance JSON for a specific event from Luma with pagination support.

    Fetches all guest pages using the Luma API's pagination (has_more, next_cursor).
    Combines all entries into a single JSON file.

    Args:
        event_api_id: Luma event API ID
        save_path: Path to save the JSON file
    Returns: True if successful, False otherwise
    """
    headers = {
        'Authorization': f'Bearer {LUMA_API_KEY}',
        'Content-Type': 'application/json'
    }

    try:
        # Luma API guests endpoint
        url = f'{LUMA_API_BASE_URL}/event/get-guests'

        # Pagination: collect all entries across multiple pages
        all_entries = []
        next_cursor = None
        page = 1

        while True:
            # Build request parameters
            params = {'event_api_id': event_api_id}
            if next_cursor:
                params['pagination_cursor'] = next_cursor

            # Make API request
            response = requests.get(url, headers=headers, params=params, timeout=60)
            response.raise_for_status()
            data = response.json()

            # Extract and accumulate entries from this page
            entries = data.get('entries', [])
            all_entries.extend(entries)

            logging.info(f"  Page {page}: fetched {len(entries)} guests (total so far: {len(all_entries)})")

            # Check if there are more pages
            if not data.get('has_more', False):
                break

            # Get cursor for next page
            next_cursor = data.get('next_cursor')
            if not next_cursor:
                logging.warning(f"has_more is true but next_cursor is missing, stopping pagination")
                break

            page += 1

        # Build complete response with all guests
        complete_response = {
            'entries': all_entries,
            'has_more': False,
            'total_count': len(all_entries)
        }

        # Save combined JSON response
        with open(save_path, 'w') as f:
            json.dump(complete_response, f, indent=2)

        logging.info(f"Downloaded {len(all_entries)} total guests for event {event_api_id} ({page} page(s))")
        return True

    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to download JSON for event {event_api_id}: {e}")
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


def update_event_if_changed(cursor, db_event_id, luma_event):
    """
    Update event fields that have changed from Luma API
    Only updates fields that differ from current database values
    Args:
        cursor: Database cursor
        db_event_id: Database event ID
        luma_event: Luma event dictionary
    """
    # Extract fields from Luma event
    event_name = luma_event.get('name', '')[:100]  # Respect VARCHAR(100) limit
    timezone_str = luma_event.get('timezone')
    start_datetime = parse_luma_datetime(luma_event.get('start_at'), timezone_str)
    description = luma_event.get('description', '')
    signup_url = luma_event.get('url', '')
    cover_image_url = luma_event.get('cover_url', '')

    # Extract location from geo_address_json
    geo_location = luma_event.get('geo_address_json', {})
    location_str = geo_location.get('city', 'TBD') if isinstance(geo_location, dict) else 'TBD'
    location_str = location_str[:100]  # Respect VARCHAR(100) limit

    # Fetch current values from database
    cursor.execute("""
        SELECT event_name, start_datetime, description, location, rsvp_link, speaker_headshot_url
        FROM events
        WHERE id = %s
    """, (db_event_id,))

    result = cursor.fetchone()
    if not result:
        logging.warning(f"Event ID {db_event_id} not found in database")
        return

    current_name, current_start, current_desc, current_location, current_rsvp, current_headshot = result

    # Compare each field and track changes
    changed_fields = []
    update_parts = []
    update_values = []

    # Compare event_name
    if current_name != event_name:
        changed_fields.append('event_name')
        update_parts.append('event_name = %s')
        update_values.append(event_name)

    # Compare start_datetime (handle potential microsecond differences)
    if current_start != start_datetime:
        changed_fields.append('start_datetime')
        update_parts.append('start_datetime = %s')
        update_values.append(start_datetime)

    # Compare description
    if current_desc != description:
        changed_fields.append('description')
        update_parts.append('description = %s')
        update_values.append(description)

    # Compare location
    if current_location != location_str:
        changed_fields.append('location')
        update_parts.append('location = %s')
        update_values.append(location_str)

    # Compare rsvp_link
    if current_rsvp != signup_url:
        changed_fields.append('rsvp_link')
        update_parts.append('rsvp_link = %s')
        update_values.append(signup_url)

    # Compare speaker_headshot_url
    if current_headshot != cover_image_url:
        changed_fields.append('speaker_headshot_url')
        update_parts.append('speaker_headshot_url = %s')
        update_values.append(cover_image_url)

    # Only execute UPDATE if there are actual changes
    if changed_fields:
        update_query = f"UPDATE events SET {', '.join(update_parts)} WHERE id = %s"
        update_values.append(db_event_id)

        cursor.execute(update_query, update_values)

        field_list = ', '.join(changed_fields)
        field_count = len(changed_fields)
        logging.info(f"Updated event ID {db_event_id}: {field_list} ({field_count} field{'s' if field_count != 1 else ''})")


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
        six_hours_ago = now - timedelta(hours=6)

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
                    update_event_if_changed(cursor, db_event_id, luma_event)

            elif start_datetime < six_hours_ago:
                # Past event (>6 hours old)
                if not exists:
                    # Create the event first
                    db_event_id = create_event(cursor, luma_event)
                    attendance = 0

                # Check if needs attendance processing
                if attendance == 0:
                    # Download JSON to temp file
                    temp_file = tempfile.NamedTemporaryFile(
                        mode='w',
                        suffix='.json',
                        delete=False,
                        prefix=f'luma_event_{luma_event_id}_'
                    )
                    temp_path = temp_file.name
                    temp_file.close()

                    if download_event_json(luma_event_id, temp_path):
                        events_to_process.append({
                            'event_id': db_event_id,
                            'json_path': temp_path,
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

#!/usr/bin/env python3
"""
Export approved guest list from Luma API to CSV
Exports: user_first_name, user_last_name, user_name, email, phone_number,
and all registration form answers as separate columns
"""

import os
import sys
import re
import requests
import pandas as pd
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
LUMA_API_KEY = os.getenv('LUMA_API_KEY')
LUMA_CALENDAR_ID = os.getenv('LUMA_CALENDAR_ID')
LUMA_API_BASE_URL = 'https://public-api.luma.com/v1'

# Validate configuration
if not LUMA_API_KEY:
    print("Error: LUMA_API_KEY not found in environment variables")
    sys.exit(1)

if not LUMA_CALENDAR_ID:
    print("Error: LUMA_CALENDAR_ID not found in environment variables")
    sys.exit(1)


def get_recent_events(limit=5):
    """
    Fetch recent events from Luma calendar

    Args:
        limit: Number of recent events to return (default 5)

    Returns:
        List of event dictionaries sorted by date (most recent first)
    """
    headers = {
        'Authorization': f'Bearer {LUMA_API_KEY}',
        'Content-Type': 'application/json'
    }

    url = f'{LUMA_API_BASE_URL}/calendar/list-events'
    params = {'calendar_api_id': LUMA_CALENDAR_ID}

    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        # Extract events from entries
        events = []
        for entry in data.get('entries', []):
            event = entry.get('event', {})
            if event:
                events.append({
                    'api_id': event.get('api_id'),
                    'name': event.get('name'),
                    'start_at': event.get('start_at'),
                    'url': event.get('url')
                })

        # Sort by start date (most recent first)
        events.sort(key=lambda x: x['start_at'], reverse=True)

        # Return limited number
        return events[:limit]

    except requests.exceptions.RequestException as e:
        print(f"Error fetching events: {e}")
        sys.exit(1)


def get_event_guests(event_api_id):
    """
    Fetch all guests for an event with pagination support

    Args:
        event_api_id: Luma event ID (e.g., 'evt-ABC123')

    Returns:
        List of all guest dictionaries
    """
    headers = {
        'Authorization': f'Bearer {LUMA_API_KEY}',
        'Content-Type': 'application/json'
    }

    url = f'{LUMA_API_BASE_URL}/event/get-guests'
    all_guests = []
    next_cursor = None
    page = 1

    print("\nFetching guests...")

    while True:
        # Build params
        params = {'event_api_id': event_api_id}
        if next_cursor:
            params['pagination_cursor'] = next_cursor

        try:
            # Make request
            response = requests.get(url, headers=headers, params=params, timeout=60)
            response.raise_for_status()
            data = response.json()

            # Extract guest objects from entries
            entries = data.get('entries', [])
            for entry in entries:
                guest = entry.get('guest', {})
                if guest:
                    all_guests.append(guest)

            print(f"  Page {page}: {len(entries)} guests (total: {len(all_guests)})")

            # Check for more pages
            if not data.get('has_more', False):
                break

            next_cursor = data.get('next_cursor')
            if not next_cursor:
                break

            page += 1

        except requests.exceptions.RequestException as e:
            print(f"Error fetching guests (page {page}): {e}")
            sys.exit(1)

    return all_guests


def extract_guest_data(guests):
    """
    Extract guest data and all registration answers

    Args:
        guests: List of guest dictionaries from Luma API

    Returns:
        pandas DataFrame with guest data
    """
    guest_records = []

    for guest in guests:
        # Extract base fields
        record = {
            'user_first_name': guest.get('user_first_name', ''),
            'user_last_name': guest.get('user_last_name', ''),
            'user_name': guest.get('user_name', ''),
            'email': guest.get('email', ''),
            'phone_number': guest.get('phone_number', '')
        }

        # Extract all registration answers
        registration_answers = guest.get('registration_answers', [])
        for answer in registration_answers:
            label = answer.get('label')
            value = answer.get('value')

            if label and value is not None:
                record[label] = value

        guest_records.append(record)

    # Convert to DataFrame
    df = pd.DataFrame(guest_records)

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


def export_guest_list():
    """
    Main function to export guest list
    """
    print("=" * 60)
    print("LUMA GUEST LIST EXPORTER")
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
        start_date = event['start_at'][:10] if event['start_at'] else 'Unknown date'
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

    # Fetch guests
    all_guests = get_event_guests(selected_event['api_id'])
    print(f"\nTotal guests fetched: {len(all_guests)}")

    # Filter for approved guests only
    approved_guests = [
        guest for guest in all_guests
        if guest.get('approval_status', '').lower() == 'approved'
    ]
    print(f"Approved guests: {len(approved_guests)}")

    if not approved_guests:
        print("\nNo approved guests found for this event.")
        sys.exit(0)

    # Extract guest data
    print("\nExtracting guest data...")
    df = extract_guest_data(approved_guests)

    # Create test_output directory at project root (same level as extra/)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)  # Go up one level from extra/
    output_dir = os.path.join(project_root, "test_output")
    os.makedirs(output_dir, exist_ok=True)

    # Generate filename
    sanitized_name = sanitize_filename(selected_event['name'])
    output_filename = os.path.join(output_dir, f"{sanitized_name}_guest_list.csv")

    # Export to CSV
    df.to_csv(output_filename, index=False)

    # Display summary
    print("\n" + "=" * 60)
    print("EXPORT COMPLETE")
    print("=" * 60)
    print(f"Event: {selected_event['name']}")
    print(f"Approved guests exported: {len(approved_guests)}")
    print(f"Output file: {output_filename}")
    print(f"Columns: {len(df.columns)}")
    print(f"\nColumns included:")
    for col in df.columns:
        print(f"  - {col}")
    print("=" * 60)


if __name__ == '__main__':
    export_guest_list()

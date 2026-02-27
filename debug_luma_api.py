#!/usr/bin/env python3
"""
Debug script to inspect Luma API JSON structure
Calls the /event/get-guests endpoint and displays the response structure
"""

import os
import json
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
LUMA_API_KEY = os.getenv('LUMA_API_KEY')
LUMA_API_BASE_URL = 'https://public-api.luma.com/v1'
EVENT_ID = 'evt-JJ3lGn0K2BgQy4t'  # The event from your logs

def main():
    if not LUMA_API_KEY:
        print("❌ ERROR: LUMA_API_KEY not found in environment variables")
        return

    headers = {
        'Authorization': f'Bearer {LUMA_API_KEY}',
        'Content-Type': 'application/json'
    }

    print(f"🔍 Fetching guest data for event: {EVENT_ID}")
    print(f"📡 Endpoint: {LUMA_API_BASE_URL}/event/get-guests")
    print(f"🔑 Using API Key: {LUMA_API_KEY[:20]}...")
    print("=" * 80)

    try:
        url = f'{LUMA_API_BASE_URL}/event/get-guests'
        params = {'event_api_id': EVENT_ID}

        response = requests.get(url, headers=headers, params=params, timeout=60)
        response.raise_for_status()

        data = response.json()

        print("\n✅ SUCCESS! API call returned data\n")
        print("=" * 80)
        print("FULL JSON RESPONSE:")
        print("=" * 80)
        print(json.dumps(data, indent=2))
        print("=" * 80)

        # Analyze structure
        print("\n📊 STRUCTURE ANALYSIS:")
        print("=" * 80)
        print(f"Top-level keys: {list(data.keys())}")

        # Check for different possible array keys
        for key in ['entries', 'guests', 'data', 'items', 'results']:
            if key in data:
                print(f"\n✓ Found array key: '{key}'")
                array_data = data[key]
                if isinstance(array_data, list) and len(array_data) > 0:
                    print(f"  Array length: {len(array_data)}")
                    print(f"  First item keys: {list(array_data[0].keys())}")
                    print(f"\n  First item structure:")
                    print(json.dumps(array_data[0], indent=4))
                break
        else:
            print("\n⚠️  No common array key found. Full data structure:")
            print(json.dumps(data, indent=2))

    except requests.exceptions.RequestException as e:
        print(f"\n❌ ERROR: Failed to fetch data from Luma API")
        print(f"Error: {e}")
        if hasattr(e.response, 'text'):
            print(f"Response: {e.response.text}")

if __name__ == '__main__':
    main()

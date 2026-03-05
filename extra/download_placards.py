#!/usr/bin/env python3
"""
Download placard PDFs from Railway database.
Exports PDFs for all events that have generated placards to the extra/placards directory.
"""

import os
import sys
import psycopg2
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


def get_db_connection():
    """Establish connection to Railway PostgreSQL database."""
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
        print(f"Error: Failed to connect to database: {e}", file=sys.stderr)
        sys.exit(1)


def sanitize_filename(name):
    """Sanitize event name for use in filename."""
    # Replace invalid characters with underscores
    safe_name = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in name)
    # Replace spaces with underscores and remove multiple consecutive underscores
    safe_name = safe_name.replace(' ', '_')
    while '__' in safe_name:
        safe_name = safe_name.replace('__', '_')
    return safe_name.strip('_')


def download_placards():
    """Download all placards from database and save to extra/placards directory."""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Query events with placards
        query = """
            SELECT id, event_name, placard_pdf, start_datetime
            FROM events
            WHERE placard_pdf IS NOT NULL
            ORDER BY start_datetime DESC
        """

        cursor.execute(query)
        results = cursor.fetchall()

        if not results:
            print("No placards found in database.")
            return

        # Create output directory
        output_dir = os.path.join(os.path.dirname(__file__), 'placards')
        os.makedirs(output_dir, exist_ok=True)

        print(f"Downloading {len(results)} placard(s) to {output_dir}/\n")

        # Save each placard
        downloaded_count = 0
        for event_id, event_name, pdf_binary, start_datetime in results:
            # Sanitize filename
            safe_name = sanitize_filename(event_name)

            # Create filename with event ID and name
            filename = f"event_{event_id}_{safe_name}.pdf"
            filepath = os.path.join(output_dir, filename)

            # Write PDF
            try:
                with open(filepath, 'wb') as f:
                    f.write(pdf_binary)

                file_size_kb = len(pdf_binary) / 1024
                print(f"✓ Downloaded: {filename} ({file_size_kb:.1f} KB)")
                downloaded_count += 1
            except Exception as e:
                print(f"✗ Error saving {filename}: {e}", file=sys.stderr)

        print(f"\nSuccessfully downloaded {downloaded_count}/{len(results)} placard(s)")

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    download_placards()

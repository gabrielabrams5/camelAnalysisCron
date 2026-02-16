#!/usr/bin/env python3
"""
Generate placard PDFs for all events in the analysis CSV and store them in the database.

This script:
1. Reads all events from event_analysis_all.csv
2. For each event:
   - Transforms the data to placard format
   - Builds the React app and generates a PDF
   - Stores the PDF in the database
   - Optionally saves a copy with event-specific name (if --save-pdfs-dir is provided)
   - Deletes the processed row from the CSV
3. Handles errors gracefully and continues processing
"""

import argparse
import csv
import os
import subprocess
import sys
import psycopg2
import pandas as pd
import shutil
from pathlib import Path
from dotenv import load_dotenv


def connect_to_db():
    """Connect to Railway PostgreSQL database."""
    load_dotenv()

    conn = psycopg2.connect(
        host=os.getenv('PGHOST'),
        port=os.getenv('PGPORT'),
        database=os.getenv('PGDATABASE'),
        user=os.getenv('PGUSER'),
        password=os.getenv('PGPASSWORD')
    )

    return conn


def transform_event(event_id, input_csv, placard_dir):
    """
    Run the transform script to convert event analysis to placard format.

    Args:
        event_id: Event ID to transform
        input_csv: Path to input CSV file
        placard_dir: Path to placard_generation directory

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        output_csv = os.path.join(placard_dir, 'public', 'event_data.csv')
        transform_script = os.path.join(os.path.dirname(__file__), 'transform_to_placard_csv.py')

        result = subprocess.run(
            [
                'python3',
                transform_script,
                '--event-id', str(event_id),
                '--input-csv', input_csv,
                '--output-csv', output_csv
            ],
            capture_output=True,
            text=True,
            check=True
        )

        print(f"  ✓ Transformed event {event_id} to placard format")
        return True

    except subprocess.CalledProcessError as e:
        print(f"  ✗ Error transforming event {event_id}: {e.stderr}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"  ✗ Unexpected error transforming event {event_id}: {e}", file=sys.stderr)
        return False


def generate_pdf(placard_dir):
    """
    Build the React app and generate a PDF using Node.js.

    Args:
        placard_dir: Path to placard_generation directory

    Returns:
        Path to generated PDF if successful, None otherwise
    """
    try:
        # Change to placard directory
        original_dir = os.getcwd()
        os.chdir(placard_dir)

        # Run the generate-pdf script
        result = subprocess.run(
            ['node', 'generate-pdf.mjs'],
            capture_output=True,
            text=True,
            check=True
        )

        print(f"  ✓ Generated PDF")

        # Return to original directory
        os.chdir(original_dir)

        # Return path to generated PDF
        pdf_path = os.path.join(placard_dir, 'event-report.pdf')
        if os.path.exists(pdf_path):
            return pdf_path
        else:
            print(f"  ✗ PDF file not found at {pdf_path}", file=sys.stderr)
            return None

    except subprocess.CalledProcessError as e:
        print(f"  ✗ Error generating PDF: {e.stderr}", file=sys.stderr)
        os.chdir(original_dir)
        return None
    except Exception as e:
        print(f"  ✗ Unexpected error generating PDF: {e}", file=sys.stderr)
        os.chdir(original_dir)
        return None


def store_pdf_in_db(conn, event_id, pdf_path):
    """
    Store the PDF binary in the database.

    Args:
        conn: Database connection
        event_id: Event ID
        pdf_path: Path to PDF file

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        with open(pdf_path, 'rb') as f:
            pdf_binary = f.read()

        cur = conn.cursor()
        cur.execute(
            "UPDATE events SET placard_pdf = %s WHERE id = %s",
            (psycopg2.Binary(pdf_binary), event_id)
        )
        conn.commit()
        cur.close()

        print(f"  ✓ Stored PDF in database for event {event_id} ({len(pdf_binary)} bytes)")
        return True

    except Exception as e:
        print(f"  ✗ Error storing PDF in database: {e}", file=sys.stderr)
        conn.rollback()
        return False


def save_pdf_copy(pdf_path, output_dir, event_id, event_name):
    """
    Save a copy of the PDF to the output directory with an event-specific name.

    Args:
        pdf_path: Path to the generated PDF
        output_dir: Directory to save the PDF copy
        event_id: Event ID
        event_name: Event name

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Create output directory if it doesn't exist
        os.makedirs(output_dir, exist_ok=True)

        # Clean event name for filename (remove special characters)
        clean_name = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in event_name)
        clean_name = clean_name.replace(' ', '_')

        # Create filename with event ID and name
        filename = f"event_{event_id}_{clean_name}.pdf"
        output_path = os.path.join(output_dir, filename)

        # Copy the PDF
        shutil.copy2(pdf_path, output_path)

        print(f"  ✓ Saved PDF to {output_path}")
        return True

    except Exception as e:
        print(f"  ✗ Error saving PDF copy: {e}", file=sys.stderr)
        return False


def delete_row_from_csv(csv_path, event_id):
    """
    Delete the processed row from the CSV file.

    Args:
        csv_path: Path to CSV file
        event_id: Event ID to delete

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Read all rows
        df = pd.read_csv(csv_path)

        # Filter out the processed event
        df_filtered = df[df['event_id'] != event_id]

        # Write back to CSV
        df_filtered.to_csv(csv_path, index=False)

        print(f"  ✓ Deleted event {event_id} from CSV")
        return True

    except Exception as e:
        print(f"  ✗ Error deleting row from CSV: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(
        description='Generate placard PDFs for all events and store in database'
    )
    parser.add_argument(
        '--input-csv',
        default='./event_analysis_all.csv',
        help='Input CSV file path (default: ./event_analysis_all.csv)'
    )
    parser.add_argument(
        '--placard-dir',
        default='./placard_generation',
        help='Path to placard_generation directory (default: ./placard_generation)'
    )
    parser.add_argument(
        '--save-pdfs-dir',
        default=None,
        help='Optional: Directory to save PDF copies with event-specific names. If not provided, PDFs are only stored in the database.'
    )

    args = parser.parse_args()

    # Resolve paths
    input_csv = os.path.abspath(args.input_csv)
    placard_dir = os.path.abspath(args.placard_dir)
    save_pdfs_dir = os.path.abspath(args.save_pdfs_dir) if args.save_pdfs_dir else None

    # Verify paths exist
    if not os.path.exists(input_csv):
        print(f"Error: Input CSV not found: {input_csv}", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(placard_dir):
        print(f"Error: Placard directory not found: {placard_dir}", file=sys.stderr)
        sys.exit(1)

    # Log whether we're saving PDFs
    if save_pdfs_dir:
        print(f"PDFs will be saved to: {save_pdfs_dir}")
    else:
        print("PDFs will only be stored in the database (not saved to disk)")

    # Connect to database
    print("Connecting to database...")
    try:
        conn = connect_to_db()
        print("✓ Connected to database\n")
    except Exception as e:
        print(f"Error connecting to database: {e}", file=sys.stderr)
        sys.exit(1)

    # Read CSV
    try:
        df = pd.read_csv(input_csv)
        total_events = len(df)
        print(f"Found {total_events} events to process\n")
    except Exception as e:
        print(f"Error reading CSV: {e}", file=sys.stderr)
        conn.close()
        sys.exit(1)

    # Process each event
    processed = 0
    failed = 0

    for idx, row in df.iterrows():
        event_id = row['event_id']
        event_name = row.get('event_name', 'Unknown')

        print(f"[{idx + 1}/{total_events}] Processing event {event_id}: {event_name}")

        # Step 1: Transform to placard format
        if not transform_event(event_id, input_csv, placard_dir):
            print(f"  ⚠ Skipping event {event_id} due to transform error\n")
            failed += 1
            continue

        # Step 2: Generate PDF
        pdf_path = generate_pdf(placard_dir)
        if not pdf_path:
            print(f"  ⚠ Skipping event {event_id} due to PDF generation error\n")
            failed += 1
            continue

        # Step 3: Store in database
        if not store_pdf_in_db(conn, event_id, pdf_path):
            print(f"  ⚠ Skipping event {event_id} due to database error\n")
            failed += 1
            continue

        # Step 4: Optionally save PDF copy to directory
        if save_pdfs_dir:
            if not save_pdf_copy(pdf_path, save_pdfs_dir, event_id, event_name):
                print(f"  ⚠ Warning: Could not save PDF copy for event {event_id}")
                # Don't fail - the PDF is already in the database

        # Step 5: Delete row from CSV
        if not delete_row_from_csv(input_csv, event_id):
            print(f"  ⚠ Warning: Could not delete row for event {event_id} from CSV")
            # Don't fail - the PDF is already stored
            # The row will remain and might be reprocessed next time

        processed += 1
        print(f"  ✅ Successfully processed event {event_id}\n")

    # Summary
    print("\n" + "=" * 60)
    print(f"Processing complete!")
    print(f"  Total events: {total_events}")
    print(f"  Successfully processed: {processed}")
    print(f"  Failed: {failed}")
    print("=" * 60)

    conn.close()


if __name__ == "__main__":
    main()

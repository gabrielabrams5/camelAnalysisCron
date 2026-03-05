#!/usr/bin/env python3
"""
Find and merge duplicate people records based on matching identifiers.
Identifies duplicates by matching school_email, personal_email, or phone_number.
Interactive mode: prompts for confirmation before each merge.
"""

import os
import sys
import psycopg2
import psycopg2.extras
from psycopg2 import sql
from dotenv import load_dotenv
import argparse
from typing import List, Dict, Any, Set, Tuple

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


def find_duplicate_groups(conn) -> List[List[Dict[str, Any]]]:
    """
    Find groups of duplicate people based on matching identifiers.
    Returns a list of duplicate groups, where each group is a list of person records.
    """
    cursor = conn.cursor()

    # Get all people with their identifiers
    cursor.execute("""
        SELECT id, first_name, last_name, preferred_name, gender, class_year,
               is_jewish, school, additional_info, school_email, personal_email,
               preferred_email, phone_number, event_attendance_count, event_rsvp_count
        FROM people
        ORDER BY id
    """)

    all_people = []
    for row in cursor.fetchall():
        all_people.append({
            'id': row[0],
            'first_name': row[1],
            'last_name': row[2],
            'preferred_name': row[3],
            'gender': row[4],
            'class_year': row[5],
            'is_jewish': row[6],
            'school': row[7],
            'additional_info': row[8],
            'school_email': row[9],
            'personal_email': row[10],
            'preferred_email': row[11],
            'phone_number': row[12],
            'event_attendance_count': row[13],
            'event_rsvp_count': row[14]
        })

    # Build a mapping of identifier -> person IDs
    school_email_map = {}
    personal_email_map = {}
    phone_map = {}
    name_map = {}

    for person in all_people:
        pid = person['id']

        # Group by school email
        if person['school_email']:
            email = person['school_email'].lower().strip()
            if email not in school_email_map:
                school_email_map[email] = []
            school_email_map[email].append(pid)

        # Group by personal email
        if person['personal_email']:
            email = person['personal_email'].lower().strip()
            if email not in personal_email_map:
                personal_email_map[email] = []
            personal_email_map[email].append(pid)

        # Group by phone number
        if person['phone_number']:
            phone = person['phone_number'].strip()
            if phone not in phone_map:
                phone_map[phone] = []
            phone_map[phone].append(pid)

        # Group by exact name match (both first and last name)
        if person['first_name'] and person['last_name']:
            first = person['first_name'].lower().strip()
            last = person['last_name'].lower().strip()
            if first and last:  # Ensure both are non-empty
                name_key = (first, last)
                if name_key not in name_map:
                    name_map[name_key] = []
                name_map[name_key].append(pid)

    # Find connected components (groups of people who share any identifier)
    person_id_to_people = {p['id']: p for p in all_people}
    visited = set()
    duplicate_groups = []

    def find_connected_group(start_id: int) -> Set[int]:
        """DFS to find all people connected to start_id through shared identifiers."""
        stack = [start_id]
        group = set()

        while stack:
            pid = stack.pop()
            if pid in group:
                continue
            group.add(pid)

            person = person_id_to_people[pid]

            # Add all people who share school email
            if person['school_email']:
                email = person['school_email'].lower().strip()
                for connected_id in school_email_map.get(email, []):
                    if connected_id not in group:
                        stack.append(connected_id)

            # Add all people who share personal email
            if person['personal_email']:
                email = person['personal_email'].lower().strip()
                for connected_id in personal_email_map.get(email, []):
                    if connected_id not in group:
                        stack.append(connected_id)

            # Add all people who share phone number
            if person['phone_number']:
                phone = person['phone_number'].strip()
                for connected_id in phone_map.get(phone, []):
                    if connected_id not in group:
                        stack.append(connected_id)

            # Add all people who share exact name (first and last)
            if person['first_name'] and person['last_name']:
                first = person['first_name'].lower().strip()
                last = person['last_name'].lower().strip()
                if first and last:
                    name_key = (first, last)
                    for connected_id in name_map.get(name_key, []):
                        if connected_id not in group:
                            stack.append(connected_id)

        return group

    # Find all duplicate groups
    for person in all_people:
        pid = person['id']
        if pid in visited:
            continue

        group_ids = find_connected_group(pid)
        visited.update(group_ids)

        # Only include groups with 2+ people (duplicates)
        if len(group_ids) >= 2:
            group_records = [person_id_to_people[gid] for gid in sorted(group_ids)]
            duplicate_groups.append(group_records)

    cursor.close()
    return duplicate_groups


def merge_person_data(conn, records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Merge data from multiple person records, preferring non-NULL values.
    Primary record (lowest ID) is used as the base.
    Calculates actual expected post-merge counts for event_attendance_count and event_rsvp_count.
    """
    # Start with the primary record (lowest ID)
    merged = records[0].copy()

    # For each field, prefer non-NULL values from any record
    for record in records[1:]:
        for key, value in record.items():
            if key == 'id':
                continue  # Keep primary ID
            if merged[key] is None and value is not None:
                merged[key] = value

    # Calculate actual expected post-merge counts
    cursor = conn.cursor()
    person_ids = [r['id'] for r in records]

    # Count checked-in attendance records across all duplicate person IDs
    cursor.execute("""
        SELECT COUNT(*)
        FROM attendance
        WHERE person_id = ANY(%s) AND checked_in = true
    """, (person_ids,))
    merged['event_attendance_count'] = cursor.fetchone()[0]

    # Count all attendance records (RSVPs) across all duplicate person IDs
    cursor.execute("""
        SELECT COUNT(*)
        FROM attendance
        WHERE person_id = ANY(%s)
    """, (person_ids,))
    merged['event_rsvp_count'] = cursor.fetchone()[0]

    cursor.close()
    return merged


def display_person(person: Dict[str, Any], label: str = ""):
    """Display a person record in a readable format."""
    if label:
        print(f"  {label}:")
    print(f"    ID: {person['id']}")
    print(f"    Name: {person['first_name']} {person['last_name']}", end="")
    if person['preferred_name']:
        print(f" (preferred: {person['preferred_name']})", end="")
    print()
    print(f"    Class Year: {person['class_year']}")
    print(f"    School Email: {person['school_email']}")
    print(f"    Personal Email: {person['personal_email']}")
    print(f"    Preferred Email: {person['preferred_email']}")
    print(f"    Phone: {person['phone_number']}")
    print(f"    School: {person['school']}")
    print(f"    Gender: {person['gender']}")
    print(f"    Is Jewish: {person['is_jewish']}")
    print(f"    Event Attendance: {person['event_attendance_count']}")
    print(f"    Event RSVPs: {person['event_rsvp_count']}")
    if person['additional_info']:
        print(f"    Additional Info: {person['additional_info']}")


def get_related_record_counts(conn, person_id: int) -> Dict[str, int]:
    """Get counts of related records for a person."""
    cursor = conn.cursor()

    counts = {}

    # Attendance records
    cursor.execute("SELECT COUNT(*) FROM attendance WHERE person_id = %s", (person_id,))
    counts['attendance'] = cursor.fetchone()[0]

    # Promo codes
    cursor.execute("SELECT COUNT(*) FROM promo_codes WHERE person_id = %s", (person_id,))
    counts['promo_codes'] = cursor.fetchone()[0]

    # Event feedback
    cursor.execute("SELECT COUNT(*) FROM event_feedback WHERE person_id = %s", (person_id,))
    counts['event_feedback'] = cursor.fetchone()[0]

    cursor.close()
    return counts


def merge_duplicate_group(conn, group: List[Dict[str, Any]], dry_run: bool = False) -> bool:
    """
    Merge a group of duplicate person records.
    Returns True if merge was performed, False if skipped.
    """
    print("\n" + "=" * 80)
    print(f"DUPLICATE GROUP FOUND ({len(group)} records)")
    print("=" * 80)

    # Display all duplicates
    for i, person in enumerate(group):
        related_counts = get_related_record_counts(conn, person['id'])
        label = f"Record {i+1}" + (" [PRIMARY - lowest ID]" if i == 0 else "")
        display_person(person, label)
        print(f"    Related records: {related_counts['attendance']} attendance, "
              f"{related_counts['promo_codes']} promo codes, "
              f"{related_counts['event_feedback']} feedback")
        print()

    # Show merged result with actual expected post-merge counts
    merged = merge_person_data(conn, group)
    print("-" * 80)
    display_person(merged, "MERGED RECORD (combining non-NULL values)")
    print("-" * 80)

    # Calculate total related records that will be merged
    total_attendance = sum(get_related_record_counts(conn, p['id'])['attendance'] for p in group)
    total_promo = sum(get_related_record_counts(conn, p['id'])['promo_codes'] for p in group)
    total_feedback = sum(get_related_record_counts(conn, p['id'])['event_feedback'] for p in group)

    print(f"\nTotal related records to merge:")
    print(f"  - {total_attendance} attendance records")
    print(f"  - {total_promo} promo code records")
    print(f"  - {total_feedback} event feedback records")
    print(f"\nDuplicate records to delete: {len(group) - 1}")

    if dry_run:
        print("\n[DRY RUN] Skipping actual merge")
        return False

    # Ask for confirmation
    response = input("\nMerge these records? [y/N]: ").strip().lower()
    if response != 'y':
        print("Skipped.")
        return False

    # Perform the merge
    try:
        cursor = conn.cursor()
        primary_id = group[0]['id']
        duplicate_ids = [p['id'] for p in group[1:]]

        # Update primary record with merged data
        cursor.execute("""
            UPDATE people
            SET first_name = %s, last_name = %s, preferred_name = %s,
                gender = %s, class_year = %s, is_jewish = %s, school = %s,
                additional_info = %s, school_email = %s, personal_email = %s,
                preferred_email = %s, phone_number = %s
            WHERE id = %s
        """, (
            merged['first_name'], merged['last_name'], merged['preferred_name'],
            merged['gender'], merged['class_year'], merged['is_jewish'], merged['school'],
            psycopg2.extras.Json(merged['additional_info']), merged['school_email'], merged['personal_email'],
            merged['preferred_email'], merged['phone_number'], primary_id
        ))

        # Reassign all related records to primary person with conflict handling
        for dup_id in duplicate_ids:
            # Handle attendance records with conflict detection
            # Get all attendance records for this duplicate person
            cursor.execute("""
                SELECT id, event_id, checked_in, approved, rsvp_datetime, is_first_event, invite_token_id
                FROM attendance
                WHERE person_id = %s
            """, (dup_id,))
            dup_attendance_records = cursor.fetchall()

            for dup_att in dup_attendance_records:
                dup_att_id, event_id, dup_checked_in, dup_approved, dup_rsvp_dt, dup_is_first, dup_token_id = dup_att

                # Check if primary person already has attendance for this event
                cursor.execute("""
                    SELECT id, checked_in, approved, rsvp_datetime, is_first_event, invite_token_id
                    FROM attendance
                    WHERE person_id = %s AND event_id = %s
                """, (primary_id, event_id))
                primary_att = cursor.fetchone()

                if primary_att:
                    # CONFLICT: Both attended same event - merge the data
                    primary_att_id, prim_checked_in, prim_approved, prim_rsvp_dt, prim_is_first, prim_token_id = primary_att

                    # Merge logic: prefer true for booleans, earlier datetime, non-NULL values
                    merged_checked_in = prim_checked_in or dup_checked_in
                    merged_approved = prim_approved or dup_approved
                    merged_rsvp_dt = prim_rsvp_dt if prim_rsvp_dt else dup_rsvp_dt
                    if prim_rsvp_dt and dup_rsvp_dt:
                        merged_rsvp_dt = min(prim_rsvp_dt, dup_rsvp_dt)
                    merged_is_first = prim_is_first  # Keep primary's value
                    merged_token_id = prim_token_id if prim_token_id else dup_token_id

                    # Update primary's attendance record with merged data
                    cursor.execute("""
                        UPDATE attendance
                        SET checked_in = %s, approved = %s, rsvp_datetime = %s,
                            is_first_event = %s, invite_token_id = %s
                        WHERE id = %s
                    """, (merged_checked_in, merged_approved, merged_rsvp_dt,
                          merged_is_first, merged_token_id, primary_att_id))

                    # Delete duplicate's attendance record
                    cursor.execute("DELETE FROM attendance WHERE id = %s", (dup_att_id,))
                else:
                    # NO CONFLICT: Just reassign to primary person
                    cursor.execute("UPDATE attendance SET person_id = %s WHERE id = %s",
                                  (primary_id, dup_att_id))

            # Reassign other related records (no conflicts expected)
            cursor.execute("UPDATE promo_codes SET person_id = %s WHERE person_id = %s",
                          (primary_id, dup_id))
            cursor.execute("UPDATE event_feedback SET person_id = %s WHERE person_id = %s",
                          (primary_id, dup_id))

        # Delete duplicate records
        cursor.execute("DELETE FROM people WHERE id = ANY(%s)", (duplicate_ids,))

        # Recalculate event counts for primary person
        cursor.execute("""
            UPDATE people
            SET event_attendance_count = (
                SELECT COUNT(*) FROM attendance WHERE person_id = %s AND checked_in = true
            )
            WHERE id = %s
        """, (primary_id, primary_id))

        conn.commit()
        cursor.close()

        print(f"\n✓ Successfully merged {len(group)} records into ID {primary_id}")
        return True

    except Exception as e:
        conn.rollback()
        print(f"\n✗ Error during merge: {e}", file=sys.stderr)
        return False


def main():
    """Main function to find and merge duplicate people."""
    parser = argparse.ArgumentParser(
        description="Find and merge duplicate people records in the database"
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help="Show what would be merged without making changes"
    )
    args = parser.parse_args()

    print("=" * 80)
    print("DUPLICATE PEOPLE FINDER AND MERGER")
    if args.dry_run:
        print("[DRY RUN MODE - No changes will be made]")
    print("=" * 80)
    print()

    conn = get_db_connection()

    try:
        # Find all duplicate groups
        print("Searching for duplicate people...")
        duplicate_groups = find_duplicate_groups(conn)

        if not duplicate_groups:
            print("\nNo duplicate records found!")
            return

        print(f"\nFound {len(duplicate_groups)} duplicate group(s)\n")

        # Process each group
        merged_count = 0
        skipped_count = 0

        for group in duplicate_groups:
            if merge_duplicate_group(conn, group, dry_run=args.dry_run):
                merged_count += 1
            else:
                skipped_count += 1

        # Summary
        print("\n" + "=" * 80)
        print("SUMMARY")
        print("=" * 80)
        print(f"Total duplicate groups found: {len(duplicate_groups)}")
        if not args.dry_run:
            print(f"Merged: {merged_count}")
            print(f"Skipped: {skipped_count}")
        else:
            print("[DRY RUN - No changes were made]")
        print("=" * 80)

    finally:
        conn.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Script to add a person to the database.
Prompts for email (required) and optional first_name and last_name.
"""

import os
import sys
import psycopg2
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Database configuration
DB_CONFIG = {
    'host': os.getenv('PGHOST'),
    'port': os.getenv('PGPORT'),
    'database': os.getenv('PGDATABASE'),
    'user': os.getenv('PGUSER'),
    'password': os.getenv('PGPASSWORD')
}


def get_db_connection():
    """Create and return a database connection."""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        return conn
    except Exception as e:
        print(f"Error: Failed to connect to database: {e}")
        sys.exit(1)


def check_duplicate_email(conn, email):
    """
    Check if email already exists in database.

    Args:
        conn: Database connection
        email: Email address to check

    Returns:
        Person ID if duplicate found, None otherwise
    """
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT id, first_name, last_name, school_email, personal_email
            FROM people
            WHERE LOWER(school_email) = LOWER(%s)
               OR LOWER(personal_email) = LOWER(%s)
               OR LOWER(preferred_email) = LOWER(%s)
            LIMIT 1
        """, (email, email, email))

        result = cursor.fetchone()
        return result
    finally:
        cursor.close()


def add_person(first_name, last_name, email):
    """
    Add a person to the database.

    Args:
        first_name: First name (can be empty string)
        last_name: Last name (can be empty string)
        email: Email address (required)

    Returns:
        Person ID if successful, None otherwise
    """
    # Auto-detect email field based on domain
    is_edu_email = email.lower().endswith('.edu')

    conn = get_db_connection()

    # Check for duplicate
    duplicate = check_duplicate_email(conn, email)
    if duplicate:
        person_id, dup_first, dup_last, dup_school_email, dup_personal_email = duplicate
        print(f"\n❌ Duplicate email found!")
        print(f"   Person ID: {person_id}")
        print(f"   Name: {dup_first} {dup_last}")
        print(f"   School Email: {dup_school_email or 'N/A'}")
        print(f"   Personal Email: {dup_personal_email or 'N/A'}")
        conn.close()
        return None

    cursor = conn.cursor()

    try:
        # Build the insert query based on email type
        if is_edu_email:
            cursor.execute("""
                INSERT INTO people (first_name, last_name, school_email)
                VALUES (%s, %s, %s)
                RETURNING id
            """, (first_name, last_name, email))
        else:
            cursor.execute("""
                INSERT INTO people (first_name, last_name, personal_email)
                VALUES (%s, %s, %s)
                RETURNING id
            """, (first_name, last_name, email))

        person_id = cursor.fetchone()[0]
        conn.commit()

        print(f"\n✓ Successfully added person!")
        print(f"   Person ID: {person_id}")
        print(f"   Name: {first_name or '(empty)'} {last_name or '(empty)'}")
        print(f"   Email: {email}")
        print(f"   Email Field: {'school_email' if is_edu_email else 'personal_email'}")

        return person_id

    except Exception as e:
        conn.rollback()
        print(f"\n❌ Error inserting person: {e}")
        return None
    finally:
        cursor.close()
        conn.close()


def main():
    """Main function to run the add person script."""
    print("\n" + "="*80)
    print("ADD PERSON TO DATABASE")
    print("="*80)
    print()

    # Get email (required)
    while True:
        email = input("Email (required): ").strip()
        if email:
            break
        print("Email is required. Please enter an email address.")

    # Get first name (optional)
    first_name = input("First name (press Enter to skip): ").strip()
    if not first_name:
        first_name = ""

    # Get last name (optional)
    last_name = input("Last name (press Enter to skip): ").strip()
    if not last_name:
        last_name = ""

    # Add person to database
    add_person(first_name, last_name, email)
    print()


if __name__ == "__main__":
    main()

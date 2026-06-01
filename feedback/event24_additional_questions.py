#!/usr/bin/env python3
"""
Event 24 (Solidworks) Additional Questions Analysis

Analyzes additional registration questions for event 24, with focused statistics:
- Gender and grad year demographics with attendance conversion
- "What brings you to Camel" analysis
- Top 10 majors and clubs (fuzzy matched)
- CSV export
"""

import psycopg2
import pandas as pd
import os
import json
from dotenv import load_dotenv
from pathlib import Path
import argparse
from collections import defaultdict, Counter
from rapidfuzz import process, fuzz


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


def get_event24_data(conn):
    """
    Query all data for event 24 including additional_info.

    Returns:
        DataFrame with person data, attendance status, and additional_info
    """
    query = """
        SELECT
            e.id as event_id,
            e.event_name,
            e.start_datetime,
            p.id as person_id,
            p.first_name,
            p.last_name,
            p.gender,
            p.class_year,
            p.school,
            p.additional_info,
            a.rsvp,
            a.checked_in,
            a.approved
        FROM events e
        JOIN attendance a ON e.id = a.event_id
        JOIN people p ON a.person_id = p.id
        WHERE e.id = 24
        ORDER BY p.last_name, p.first_name
    """

    df = pd.read_sql(query, conn)

    # Parse JSON additional_info column (psycopg2 may return dict or string)
    def parse_additional_info(x):
        if x is None:
            return {}
        if isinstance(x, dict):
            return x
        if isinstance(x, str):
            try:
                return json.loads(x) if x and x != 'null' else {}
            except json.JSONDecodeError:
                return {}
        return {}

    df['additional_info_parsed'] = df['additional_info'].apply(parse_additional_info)

    return df


def extract_all_questions(df):
    """
    Extract all unique questions from the additional_info field.

    Args:
        df: DataFrame with additional_info_parsed column

    Returns:
        List of unique question labels
    """
    all_questions = set()

    for info_dict in df['additional_info_parsed']:
        if isinstance(info_dict, dict):
            all_questions.update(info_dict.keys())

    return sorted(list(all_questions))


def expand_additional_info_columns(df, questions):
    """
    Expand additional_info into separate columns for each question.

    Args:
        df: DataFrame with additional_info_parsed column
        questions: List of question labels to extract

    Returns:
        DataFrame with additional columns for each question
    """
    for question in questions:
        df[f'Q: {question}'] = df['additional_info_parsed'].apply(
            lambda x: x.get(question, None) if isinstance(x, dict) else None
        )

    return df


def calculate_gender_stats(df):
    """
    Calculate gender distribution and RSVP to attendance conversion rates.

    Returns:
        Dictionary with gender statistics
    """
    stats = {}

    total = len(df)

    for gender in df['gender'].dropna().unique():
        gender_df = df[df['gender'] == gender]
        count = len(gender_df)
        attended = gender_df['checked_in'].sum()

        stats[gender] = {
            'count': count,
            'percentage': (count / total * 100) if total > 0 else 0,
            'attended': attended,
            'conversion_rate': (attended / count * 100) if count > 0 else 0
        }

    return stats


def calculate_grad_year_stats(df):
    """
    Calculate grad year distribution and RSVP to attendance conversion rates.

    Returns:
        Dictionary with grad year statistics
    """
    stats = {}

    total = len(df)

    for year in sorted(df['class_year'].dropna().unique()):
        year_df = df[df['class_year'] == year]
        count = len(year_df)
        attended = year_df['checked_in'].sum()

        stats[int(year)] = {
            'count': count,
            'percentage': (count / total * 100) if total > 0 else 0,
            'attended': attended,
            'conversion_rate': (attended / count * 100) if count > 0 else 0
        }

    return stats


def calculate_what_brings_you_stats(df, question):
    """
    Calculate comprehensive statistics for "What brings you to Camel" question.
    Handles multi-select responses (lists).

    Returns:
        Dictionary with overall, gender, and grad year breakdowns
    """
    col_name = f'Q: {question}'

    if col_name not in df.columns:
        return None

    # Extract all individual options selected (handles lists)
    all_options = []
    option_to_attendees = defaultdict(list)

    for idx, row in df.iterrows():
        response = row[col_name]

        # Check if response is None or NaN (handle arrays/lists carefully)
        if response is None:
            continue
        try:
            if pd.isna(response):
                continue
        except (ValueError, TypeError):
            # If pd.isna fails (e.g., for arrays), the response is valid
            pass

        # Handle lists (multi-select)
        if isinstance(response, list):
            options = response
        else:
            options = [response]

        for option in options:
            all_options.append(option)
            option_to_attendees[option].append({
                'attended': row['checked_in'],
                'gender': row['gender'],
                'class_year': row['class_year']
            })

    # Overall statistics
    total_respondents = df[col_name].notna().sum()
    option_counts = Counter(all_options)

    overall_stats = {}
    for option, count in option_counts.items():
        attendees = option_to_attendees[option]
        attended_count = sum(1 for a in attendees if a['attended'])

        overall_stats[option] = {
            'count': count,
            'percentage': (count / total_respondents * 100) if total_respondents > 0 else 0,
            'attended': attended_count,
            'attendance_rate': (attended_count / count * 100) if count > 0 else 0
        }

    # By gender
    gender_stats = {}
    for gender in df['gender'].dropna().unique():
        gender_total = len(df[df['gender'] == gender])
        gender_options = Counter()

        for option, attendees in option_to_attendees.items():
            gender_count = sum(1 for a in attendees if a['gender'] == gender)
            if gender_count > 0:
                gender_options[option] = gender_count

        gender_stats[gender] = {}
        for option, count in gender_options.items():
            gender_stats[gender][option] = {
                'count': count,
                'percentage': (count / gender_total * 100) if gender_total > 0 else 0
            }

    # By grad year
    grad_year_stats = {}
    for year in sorted(df['class_year'].dropna().unique()):
        year_total = len(df[df['class_year'] == year])
        year_options = Counter()

        for option, attendees in option_to_attendees.items():
            year_count = sum(1 for a in attendees if a['class_year'] == year)
            if year_count > 0:
                year_options[option] = year_count

        grad_year_stats[int(year)] = {}
        for option, count in year_options.items():
            grad_year_stats[int(year)][option] = {
                'count': count,
                'percentage': (count / year_total * 100) if year_total > 0 else 0
            }

    return {
        'overall': overall_stats,
        'by_gender': gender_stats,
        'by_grad_year': grad_year_stats
    }


def fuzzy_match_top_10(df, question, canonical_names=None):
    """
    Apply fuzzy matching to group similar responses and return top 10.

    Args:
        df: DataFrame with question column
        question: Question label
        canonical_names: Optional dict of canonical names to match against

    Returns:
        List of tuples: (canonical_name, total_count, percentage, original_responses)
    """
    col_name = f'Q: {question}'

    if col_name not in df.columns:
        return []

    # Extract all responses (handle lists by expanding them)
    all_responses = []
    for response in df[col_name].dropna():
        if isinstance(response, list):
            all_responses.extend(response)
        else:
            all_responses.append(str(response))

    if not all_responses:
        return []

    # Count original responses
    response_counts = Counter(all_responses)

    # If no canonical names provided, use the most common responses as canonical
    if canonical_names is None:
        canonical_names = [r for r, _ in response_counts.most_common(50)]

    # Group similar responses using fuzzy matching
    grouped = defaultdict(list)

    for response, count in response_counts.items():
        # Find best match among canonical names
        best_match = process.extractOne(
            response,
            canonical_names,
            scorer=fuzz.token_sort_ratio,
            score_cutoff=75  # 75% similarity threshold
        )

        if best_match:
            canonical = best_match[0]
            grouped[canonical].append((response, count))
        else:
            # No good match, use original as canonical
            grouped[response].append((response, count))

    # Calculate totals and create results
    results = []
    total_responses = sum(response_counts.values())

    for canonical, originals in grouped.items():
        total_count = sum(count for _, count in originals)
        percentage = (total_count / total_responses * 100) if total_responses > 0 else 0

        results.append({
            'canonical': canonical,
            'count': total_count,
            'percentage': percentage,
            'originals': originals
        })

    # Sort by count and return top 10
    results.sort(key=lambda x: x['count'], reverse=True)
    return results[:10]


def print_gender_stats(stats):
    """Print gender statistics to terminal."""
    print("\n" + "=" * 80)
    print("GENDER STATISTICS")
    print("=" * 80)

    for gender, data in sorted(stats.items()):
        print(f"\n{gender}: {data['count']} ({data['percentage']:.1f}%)")
        print(f"  RSVP → Attendance: {data['attended']}/{data['count']} ({data['conversion_rate']:.1f}%)")


def print_grad_year_stats(stats):
    """Print grad year statistics to terminal."""
    print("\n" + "=" * 80)
    print("GRAD YEAR STATISTICS")
    print("=" * 80)

    for year, data in sorted(stats.items()):
        print(f"\n{year}: {data['count']} ({data['percentage']:.1f}%)")
        print(f"  RSVP → Attendance: {data['attended']}/{data['count']} ({data['conversion_rate']:.1f}%)")


def print_what_brings_you_analysis(stats, question):
    """Print "What brings you to Camel" analysis to terminal."""
    if not stats:
        print(f"\nNo data found for question: {question}")
        return

    print("\n" + "=" * 80)
    print(f'"{question.upper()}" ANALYSIS')
    print("=" * 80)

    # Overall statistics
    print("\nOverall Selections (multi-select):")
    print("-" * 80)
    for option, data in sorted(stats['overall'].items(), key=lambda x: x[1]['count'], reverse=True):
        print(f"  {option}: {data['count']} people ({data['percentage']:.1f}%)")
        print(f"    → If selected: {data['attended']}/{data['count']} attended ({data['attendance_rate']:.1f}%)")

    # By gender
    print("\nBy Gender:")
    print("-" * 80)
    for gender, gender_data in sorted(stats['by_gender'].items()):
        gender_total = sum(d['count'] for d in gender_data.values())
        print(f"  {gender}:")
        for option, data in sorted(gender_data.items(), key=lambda x: x[1]['count'], reverse=True):
            print(f"    - {option}: {data['count']} selected ({data['percentage']:.1f}%)")

    # By grad year
    print("\nBy Grad Year:")
    print("-" * 80)
    for year, year_data in sorted(stats['by_grad_year'].items()):
        print(f"  {year}:")
        top_3 = sorted(year_data.items(), key=lambda x: x[1]['count'], reverse=True)[:3]
        for option, data in top_3:
            print(f"    - {option}: {data['count']} selected ({data['percentage']:.1f}%)")


def print_fuzzy_top_10(results, title):
    """Print fuzzy-matched top 10 results to terminal."""
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)

    if not results:
        print("\nNo data available")
        return

    for i, result in enumerate(results, 1):
        print(f"\n{i}. {result['canonical']}: {result['count']} ({result['percentage']:.1f}%)")

        # Show grouped originals if more than one
        if len(result['originals']) > 1:
            print(f"   Grouped from:")
            for original, count in sorted(result['originals'], key=lambda x: x[1], reverse=True):
                if original != result['canonical']:
                    print(f"     - {original}: {count}")


def main():
    """Main function."""
    parser = argparse.ArgumentParser(
        description='Analyze additional registration questions for Event 24 (Solidworks)'
    )
    parser.add_argument(
        '--outdir',
        type=str,
        default='.',
        help='Output directory for CSV file (default: current directory)'
    )
    parser.add_argument(
        '--csv-filename',
        type=str,
        default='event24_additional_questions.csv',
        help='Output CSV filename (default: event24_additional_questions.csv)'
    )
    args = parser.parse_args()

    print("\n" + "=" * 80)
    print("EVENT 24 (SOLIDWORKS) - ADDITIONAL QUESTIONS ANALYSIS")
    print("=" * 80)

    # Connect to database
    print("\nConnecting to database...")
    conn = connect_to_db()
    print("Connected!")

    # Load data
    print("\nLoading event 24 data...")
    df = get_event24_data(conn)

    if len(df) == 0:
        print("ERROR: No data found for event 24. Please verify the event ID.")
        conn.close()
        return

    event_name = df['event_name'].iloc[0]
    event_date = df['start_datetime'].iloc[0]

    print(f"Event: {event_name}")
    print(f"Date: {event_date}")
    print(f"Total RSVPs: {len(df)}")
    print(f"Total Attended: {df['checked_in'].sum()}")
    print(f"Attendance Rate: {df['checked_in'].sum() / len(df) * 100:.1f}%")

    # Extract questions
    print("\nDiscovering questions in additional_info field...")
    questions = extract_all_questions(df)

    if len(questions) == 0:
        print("WARNING: No additional questions found in additional_info field.")
        conn.close()
        return

    print(f"Found {len(questions)} unique questions:")
    for i, q in enumerate(questions, 1):
        print(f"  {i}. {q}")

    # Expand columns
    print("\nExpanding additional_info into columns...")
    df = expand_additional_info_columns(df, questions)

    # Save CSV
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    output_path = outdir / args.csv_filename

    # Prepare CSV output (drop the parsed dict column)
    csv_df = df.drop(columns=['additional_info_parsed', 'additional_info'])
    csv_df.to_csv(output_path, index=False)

    print(f"\nCSV exported to: {output_path.resolve()}")

    # === NEW STATISTICS ===

    # Gender statistics
    gender_stats = calculate_gender_stats(df)
    print_gender_stats(gender_stats)

    # Grad year statistics
    grad_year_stats = calculate_grad_year_stats(df)
    print_grad_year_stats(grad_year_stats)

    # "What brings you to Camel" analysis
    what_brings_question = None
    for q in questions:
        if 'what brings' in q.lower():
            what_brings_question = q
            break

    if what_brings_question:
        what_brings_stats = calculate_what_brings_you_stats(df, what_brings_question)
        print_what_brings_you_analysis(what_brings_stats, what_brings_question)

    # Top 10 majors (fuzzy matched)
    major_question = None
    for q in questions:
        if 'major' in q.lower():
            major_question = q
            break

    if major_question:
        # Define canonical major names for better grouping
        canonical_majors = [
            "Computer Science", "Mechanical Engineering", "Electrical Engineering",
            "Physics", "Mathematics", "Biology", "Chemistry",
            "Aerospace Engineering", "Chemical Engineering", "Civil Engineering",
            "Business", "Economics", "Bioengineering", "Materials Science",
            "Architecture", "Political Science", "Neuroscience"
        ]
        major_results = fuzzy_match_top_10(df, major_question, canonical_majors)
        print_fuzzy_top_10(major_results, "TOP 10 MAJORS (FUZZY MATCHED)")

    # Top 10 school clubs (fuzzy matched)
    club_question = None
    for q in questions:
        if 'club' in q.lower():
            club_question = q
            break

    if club_question:
        # Define canonical club names for better grouping
        canonical_clubs = [
            "MIT Motorsports", "Solar Electric Vehicle Team", "Rocket Team",
            "Combat Robotics", "Sloan Business Club", "Startlabs",
            "Sandbox", "Entrepreneurship Club", "VR/AR@MIT",
            "Poker Club", "FSAE", "Camel"
        ]
        club_results = fuzzy_match_top_10(df, club_question, canonical_clubs)
        print_fuzzy_top_10(club_results, "TOP 10 SCHOOL CLUBS (FUZZY MATCHED)")

    print("\n" + "=" * 80)
    print("ANALYSIS COMPLETE")
    print("=" * 80)
    print(f"\nOutputs:")
    print(f"  CSV: {output_path.resolve()}")
    print()

    conn.close()


if __name__ == '__main__':
    main()

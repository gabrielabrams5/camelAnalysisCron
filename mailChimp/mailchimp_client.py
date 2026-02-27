"""
Mailchimp API client for batch tagging event attendees and syncing full audience.
"""

import os
import re
import logging
import hashlib
from typing import List, Dict, Optional

try:
    import mailchimp_marketing as MailchimpMarketing
    from mailchimp_marketing.api_client import ApiClientError
except ImportError:
    # Allow import even if library not installed yet
    MailchimpMarketing = None
    ApiClientError = Exception


def sanitize_event_name(event_name: str) -> str:
    """
    Convert event name to safe tag format.

    Examples:
        "Camel Case Event!" -> "camel_case_event"
        "Spring 2024 Mixer" -> "spring_2024_mixer"
        "Coffee & Coding" -> "coffee_coding"

    Args:
        event_name: Raw event name string

    Returns:
        Sanitized event name (lowercase, underscores, alphanumeric only)
    """
    # Convert to lowercase
    sanitized = event_name.lower()

    # Replace spaces and common separators with underscores
    sanitized = re.sub(r'[\s\-&/]+', '_', sanitized)

    # Remove all non-alphanumeric characters except underscores
    sanitized = re.sub(r'[^a-z0-9_]', '', sanitized)

    # Replace multiple consecutive underscores with single underscore
    sanitized = re.sub(r'_+', '_', sanitized)

    # Remove leading/trailing underscores
    sanitized = sanitized.strip('_')

    return sanitized


def _get_mailchimp_client():
    """
    Initialize and return configured Mailchimp client.

    Returns:
        Configured Mailchimp client instance

    Raises:
        ValueError: If required environment variables are missing
        ImportError: If mailchimp-marketing package is not installed
    """
    if MailchimpMarketing is None:
        raise ImportError(
            "mailchimp-marketing package not installed. "
            "Install it with: pip install mailchimp-marketing"
        )

    api_key = os.getenv('MAILCHIMP_API_KEY')
    server_prefix = os.getenv('MAILCHIMP_SERVER_PREFIX')

    if not api_key:
        raise ValueError("MAILCHIMP_API_KEY environment variable not set")
    if not server_prefix:
        raise ValueError("MAILCHIMP_SERVER_PREFIX environment variable not set")

    client = MailchimpMarketing.Client()
    client.set_config({
        "api_key": api_key,
        "server": server_prefix
    })

    return client


def _subscriber_hash(email: str) -> str:
    """
    Generate MD5 hash of email for Mailchimp member ID.

    Args:
        email: Email address

    Returns:
        MD5 hash of lowercased, trimmed email
    """
    return hashlib.md5(email.strip().lower().encode("utf-8")).hexdigest()


def batch_tag_attendees(
    attendees: List[Dict[str, str]],
    event_name: str,
    audience_id: Optional[str] = None,
    tag_suffix: str = "attended"
) -> Dict[str, int]:
    """
    Batch upsert attendees to Mailchimp and tag them with event attendance.

    This function:
    1. Batch upserts all attendees to the Mailchimp audience (creates/updates)
    2. Tags each attendee with "{sanitized_event_name}_{tag_suffix}"

    Args:
        attendees: List of attendee dictionaries with keys:
            - email: Email address (required)
            - first_name: First name (optional)
            - last_name: Last name (optional)
        event_name: Name of the event (will be sanitized for tag)
        audience_id: Mailchimp audience/list ID (defaults to env var)
        tag_suffix: Suffix for the tag (default: "attended")

    Returns:
        Dictionary with counts:
            - total: Total attendees processed
            - upserted: Successfully upserted members
            - tagged: Successfully tagged members
            - errors: Number of errors encountered

    Raises:
        ValueError: If attendees list is empty or audience_id not provided
    """
    if not attendees:
        logging.warning("No attendees provided to batch_tag_attendees")
        return {"total": 0, "upserted": 0, "tagged": 0, "errors": 0}

    # Get audience ID from parameter or environment
    list_id = audience_id or os.getenv('MAILCHIMP_AUDIENCE_ID')
    if not list_id:
        raise ValueError(
            "audience_id parameter or MAILCHIMP_AUDIENCE_ID "
            "environment variable must be set"
        )

    # Sanitize event name for tag
    sanitized_event = sanitize_event_name(event_name)
    tag_name = f"{sanitized_event}_{tag_suffix}"

    logging.info(
        f"Batch tagging {len(attendees)} attendees for event '{event_name}' "
        f"with tag '{tag_name}'"
    )

    # Initialize counters
    stats = {
        "total": len(attendees),
        "upserted": 0,
        "tagged": 0,
        "errors": 0
    }

    try:
        client = _get_mailchimp_client()

        # Step 1: Batch upsert members
        members_payload = []
        for attendee in attendees:
            email = attendee.get('email')
            if not email:
                logging.warning(f"Skipping attendee without email: {attendee}")
                stats['errors'] += 1
                continue

            member_data = {
                "email_address": email,
                "status_if_new": "subscribed",
                "status": "subscribed",
            }

            # Add merge fields if names are provided
            merge_fields = {}
            if attendee.get('first_name'):
                merge_fields['FNAME'] = attendee['first_name']
            if attendee.get('last_name'):
                merge_fields['LNAME'] = attendee['last_name']

            if merge_fields:
                member_data['merge_fields'] = merge_fields

            members_payload.append(member_data)

        # Execute batch upsert
        if members_payload:
            try:
                response = client.lists.batch_list_members(
                    list_id,
                    {
                        "members": members_payload,
                        "update_existing": True
                    }
                )

                # API returns lists of members, not counts
                stats['upserted'] = (
                    len(response.get('new_members', [])) +
                    len(response.get('updated_members', []))
                )

                if response.get('errors'):
                    stats['errors'] += len(response['errors'])
                    for error in response['errors'][:5]:  # Log first 5 errors
                        logging.warning(
                            f"Batch upsert error for {error.get('email_address')}: "
                            f"{error.get('error')}"
                        )

                logging.info(
                    f"Batch upsert complete: {stats['upserted']} members "
                    f"created/updated, {response.get('errors', []).__len__()} errors"
                )

            except ApiClientError as e:
                logging.error(f"Mailchimp batch upsert failed: {e.text}")
                stats['errors'] += len(members_payload)
                return stats

        # Step 2: Tag each member individually
        for attendee in attendees:
            email = attendee.get('email')
            if not email:
                continue

            try:
                subscriber_hash = _subscriber_hash(email)
                client.lists.update_list_member_tags(
                    list_id,
                    subscriber_hash,
                    {
                        "tags": [
                            {"name": tag_name, "status": "active"}
                        ]
                    }
                )
                stats['tagged'] += 1

            except ApiClientError as e:
                logging.warning(
                    f"Failed to tag {email} with '{tag_name}': {e.text}"
                )
                stats['errors'] += 1

        logging.info(
            f"Tagging complete: {stats['tagged']}/{stats['total']} attendees "
            f"tagged with '{tag_name}'"
        )

    except Exception as e:
        logging.error(f"Unexpected error in batch_tag_attendees: {str(e)}")
        stats['errors'] = stats['total']

    return stats


def sync_full_audience(
    contacts: List[Dict[str, str]],
    audience_id: Optional[str] = None,
    batch_size: int = 500
) -> Dict[str, int]:
    """
    Sync full mailing list to Mailchimp audience (add/update contacts).

    This function performs a one-way sync from database to Mailchimp:
    - Adds new contacts to the audience
    - Updates existing contacts with latest name information
    - Does NOT remove contacts that are no longer in the database

    Args:
        contacts: List of contact dictionaries with keys:
            - email: Email address (required)
            - first_name: First name (optional)
            - last_name: Last name (optional)
        audience_id: Mailchimp audience/list ID (defaults to env var)
        batch_size: Number of contacts to process per batch (max 500)

    Returns:
        Dictionary with counts:
            - total: Total contacts processed
            - new: Successfully added new members
            - updated: Successfully updated existing members
            - errors: Number of errors encountered

    Raises:
        ValueError: If contacts list is empty or audience_id not provided
    """
    if not contacts:
        logging.warning("No contacts provided to sync_full_audience")
        return {"total": 0, "new": 0, "updated": 0, "errors": 0}

    # Get audience ID from parameter or environment
    list_id = audience_id or os.getenv('MAILCHIMP_AUDIENCE_ID')
    if not list_id:
        raise ValueError(
            "audience_id parameter or MAILCHIMP_AUDIENCE_ID "
            "environment variable must be set"
        )

    # Validate batch size
    if batch_size > 500:
        logging.warning("Batch size exceeds Mailchimp limit of 500, using 500")
        batch_size = 500

    logging.info(
        f"Syncing {len(contacts)} contacts to Mailchimp audience {list_id}"
    )

    # Initialize counters
    stats = {
        "total": len(contacts),
        "new": 0,
        "updated": 0,
        "errors": 0
    }

    try:
        client = _get_mailchimp_client()

        # Process contacts in batches
        for batch_start in range(0, len(contacts), batch_size):
            batch_end = min(batch_start + batch_size, len(contacts))
            batch_contacts = contacts[batch_start:batch_end]

            logging.info(
                f"Processing batch {batch_start // batch_size + 1}: "
                f"contacts {batch_start + 1}-{batch_end} of {len(contacts)}"
            )

            # Build members payload for this batch
            members_payload = []
            for contact in batch_contacts:
                email = contact.get('email')
                if not email:
                    logging.warning(f"Skipping contact without email: {contact}")
                    stats['errors'] += 1
                    continue

                member_data = {
                    "email_address": email,
                    "status_if_new": "subscribed",
                }

                # Add merge fields if names are provided
                merge_fields = {}
                if contact.get('first_name'):
                    merge_fields['FNAME'] = contact['first_name']
                if contact.get('last_name'):
                    merge_fields['LNAME'] = contact['last_name']

                if merge_fields:
                    member_data['merge_fields'] = merge_fields

                members_payload.append(member_data)

            # Execute batch upsert for this batch
            if members_payload:
                try:
                    response = client.lists.batch_list_members(
                        list_id,
                        {
                            "members": members_payload,
                            "update_existing": True
                        }
                    )

                    # API returns lists of members, not counts
                    batch_new = len(response.get('new_members', []))
                    batch_updated = len(response.get('updated_members', []))
                    batch_errors = len(response.get('errors', []))

                    stats['new'] += batch_new
                    stats['updated'] += batch_updated
                    stats['errors'] += batch_errors

                    logging.info(
                        f"Batch complete: {batch_new} new, {batch_updated} updated, "
                        f"{batch_errors} errors"
                    )

                    # Log first 5 errors from this batch
                    if response.get('errors'):
                        for error in response['errors'][:5]:
                            logging.warning(
                                f"Batch sync error for {error.get('email_address')}: "
                                f"{error.get('error')}"
                            )
                        if len(response['errors']) > 5:
                            logging.warning(
                                f"... and {len(response['errors']) - 5} more errors in this batch"
                            )

                except ApiClientError as e:
                    logging.error(
                        f"Mailchimp batch sync failed for batch starting at {batch_start}: "
                        f"{e.text}"
                    )
                    stats['errors'] += len(members_payload)

        # Final summary
        logging.info(
            f"Audience sync complete: {stats['new']} new contacts, "
            f"{stats['updated']} updated, {stats['errors']} errors out of "
            f"{stats['total']} total contacts"
        )

    except Exception as e:
        logging.error(f"Unexpected error in sync_full_audience: {str(e)}")
        stats['errors'] = stats['total']

    return stats

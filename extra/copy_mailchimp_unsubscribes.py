#!/usr/bin/env python3
"""
Copy unsubscribes (and bounces) from one Mailchimp audience to another.

Run this before deleting an audience whose contacts were copied into another
one: anyone who unsubscribed from (or bounced on) a campaign sent to the old
audience must not stay subscribed in the new one.

Only members that exist in the target audience as "subscribed" are changed.
Unsubscribed members are set to unsubscribed; cleaned (bounced) members are
also set to unsubscribed, since the API cannot mark a member cleaned.

Usage:
    python extra/copy_mailchimp_unsubscribes.py SOURCE_AUDIENCE_ID [TARGET_AUDIENCE_ID] [--apply]

TARGET defaults to MAILCHIMP_AUDIENCE_ID. Without --apply it only prints what
would change.
"""

import hashlib
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env'))

API_KEY = os.getenv('MAILCHIMP_API_KEY')
BASE_URL = f"https://{os.getenv('MAILCHIMP_SERVER_PREFIX')}.api.mailchimp.com/3.0"


def get_members(session, audience_id, status):
    """Return {lowercased email: email} for all members with the given status."""
    members = {}
    offset = 0
    while True:
        response = session.get(
            f'{BASE_URL}/lists/{audience_id}/members',
            params={'status': status, 'count': 500, 'offset': offset,
                    'fields': 'members.email_address,total_items'},
            timeout=120
        )
        response.raise_for_status()
        data = response.json()
        for member in data['members']:
            members[member['email_address'].lower()] = member['email_address']
        offset += len(data['members'])
        if not data['members'] or offset >= data['total_items']:
            return members


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    apply_changes = '--apply' in sys.argv
    if not args:
        print(__doc__)
        sys.exit(1)
    source_id = args[0]
    target_id = args[1] if len(args) > 1 else os.getenv('MAILCHIMP_AUDIENCE_ID')

    session = requests.Session()
    session.auth = ('x', API_KEY)

    opted_out = get_members(session, source_id, 'unsubscribed')
    opted_out.update(get_members(session, source_id, 'cleaned'))
    subscribed_in_target = get_members(session, target_id, 'subscribed')

    to_unsubscribe = sorted(e for e in opted_out if e in subscribed_in_target)
    print(f"{len(opted_out)} unsubscribed/cleaned in {source_id}; "
          f"{len(to_unsubscribe)} of them are still subscribed in {target_id}")

    if not apply_changes:
        print("Dry run - pass --apply to unsubscribe them in the target audience")
        return

    for email in to_unsubscribe:
        subscriber_hash = hashlib.md5(email.encode('utf-8')).hexdigest()
        response = session.patch(
            f'{BASE_URL}/lists/{target_id}/members/{subscriber_hash}',
            json={'status': 'unsubscribed'},
            timeout=60
        )
        if response.status_code >= 300:
            print(f"  Failed for {email}: {response.status_code} {response.text[:200]}")
    print(f"Unsubscribed {len(to_unsubscribe)} members in {target_id}")


if __name__ == '__main__':
    main()

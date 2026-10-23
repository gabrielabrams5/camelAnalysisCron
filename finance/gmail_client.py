#!/usr/bin/env python3
"""
Minimal Gmail API client (plain requests, no Google SDK): reads mail, and
sends the weekly balance summary from the same mailbox.

Authenticates with a long-lived OAuth refresh token minted once by
finance/gmail_auth.py, and exchanges it for a short-lived access token per run.

Environment variables:
    GMAIL_CLIENT_ID       OAuth client (type "Desktop app")
    GMAIL_CLIENT_SECRET
    GMAIL_REFRESH_TOKEN   from finance/gmail_auth.py
"""

import base64
import os
import time
from email.message import EmailMessage
from typing import Iterator, List, Optional, Tuple

import requests

TOKEN_URL = 'https://oauth2.googleapis.com/token'
API_BASE = 'https://gmail.googleapis.com/gmail/v1/users/me'
# Read the QuickBooks reports; send the weekly summary. Nothing else.
SCOPES = (
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.send',
)


class GmailAuthError(RuntimeError):
    pass


class GmailClient:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str, timeout: int = 30):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.timeout = timeout
        self._access_token: Optional[str] = None
        self._expires_at = 0.0

    @classmethod
    def from_env(cls) -> 'GmailClient':
        values = [os.getenv(k) for k in ('GMAIL_CLIENT_ID', 'GMAIL_CLIENT_SECRET', 'GMAIL_REFRESH_TOKEN')]
        if not all(values):
            raise GmailAuthError(
                "GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET and GMAIL_REFRESH_TOKEN must all be set "
                "(mint the token with finance/gmail_auth.py)")
        return cls(*values)

    def _token(self) -> str:
        if self._access_token and time.time() < self._expires_at - 60:
            return self._access_token
        resp = requests.post(TOKEN_URL, data={
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'refresh_token': self.refresh_token,
            'grant_type': 'refresh_token',
        }, timeout=self.timeout)
        if resp.status_code != 200:
            try:
                error = resp.json().get('error')
            except ValueError:
                error = resp.text[:200]
            if error == 'invalid_grant':
                raise GmailAuthError(
                    "GMAIL_REFRESH_TOKEN was revoked or has expired - mint a new one with "
                    "finance/gmail_auth.py and update it in Railway")
            raise GmailAuthError(f"token refresh failed: HTTP {resp.status_code} {error}")
        body = resp.json()
        self._access_token = body['access_token']
        self._expires_at = time.time() + int(body.get('expires_in', 3600))
        return self._access_token

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        resp = requests.get(API_BASE + path, params=params, timeout=self.timeout,
                            headers={'Authorization': f'Bearer {self._token()}'})
        resp.raise_for_status()
        return resp.json()

    def search(self, query: str, include_trash: bool = True) -> List[str]:
        """Message ids matching a Gmail search query, newest first."""
        ids, page_token = [], None
        while True:
            params = {'q': query, 'maxResults': 100,
                      'includeSpamTrash': 'true' if include_trash else 'false'}
            if page_token:
                params['pageToken'] = page_token
            data = self._get('/messages', params)
            ids.extend(m['id'] for m in data.get('messages', []))
            page_token = data.get('nextPageToken')
            if not page_token:
                return ids

    def get_message(self, message_id: str) -> dict:
        return self._get(f'/messages/{message_id}', {'format': 'full'})

    def mailbox(self) -> str:
        """The address this token belongs to."""
        return self._get('/profile')['emailAddress']

    def send(self, to: List[str], subject: str, text: str, html: Optional[str] = None) -> str:
        """Send a message from this mailbox. Returns the sent message's id."""
        msg = EmailMessage()
        msg['To'] = ', '.join(to)
        msg['Subject'] = subject
        msg.set_content(text)
        if html:
            msg.add_alternative(html, subtype='html')
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        resp = requests.post(API_BASE + '/messages/send', json={'raw': raw}, timeout=self.timeout,
                             headers={'Authorization': f'Bearer {self._token()}'})
        if resp.status_code == 403 and 'scope' in resp.text.lower():
            raise GmailAuthError(
                "GMAIL_REFRESH_TOKEN cannot send mail - it was minted before sending was added. "
                "Re-run finance/gmail_auth.py and update GMAIL_REFRESH_TOKEN in Railway")
        resp.raise_for_status()
        return resp.json().get('id', '')

    def attachments(self, message: dict) -> Iterator[Tuple[str, bytes]]:
        """Yield (filename, content) for every attachment of a full-format message."""
        for part in _walk_parts(message.get('payload', {})):
            filename = part.get('filename')
            body = part.get('body', {})
            if not filename:
                continue
            if body.get('attachmentId'):
                data = self._get(f"/messages/{message['id']}/attachments/{body['attachmentId']}")['data']
            elif body.get('data'):
                data = body['data']
            else:
                continue
            yield filename, _b64url(data)


def header(message: dict, name: str) -> Optional[str]:
    for h in message.get('payload', {}).get('headers', []):
        if h.get('name', '').lower() == name.lower():
            return h.get('value')
    return None


def _walk_parts(part: dict):
    yield part
    for child in part.get('parts', []) or []:
        yield from _walk_parts(child)


def _b64url(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + '=' * (-len(data) % 4))

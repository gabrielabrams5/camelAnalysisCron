#!/usr/bin/env python3
"""
One-time helper: mint the Gmail refresh token that finance/track_balance.py
(pipeline Step 9) uses to read the weekly QuickBooks P&L email.

Run it locally and approve in the browser while signed in to the mailbox that
receives the QuickBooks reports. The scope is read-only (gmail.readonly).

Setup:
1. Google Cloud console -> the project that holds camelManagement's Gmail
   OAuth client -> APIs & Services -> Credentials -> Create credentials ->
   OAuth client ID -> Application type "Desktop app".
   The consent screen must be "In production": in "Testing" Google expires
   refresh tokens after 7 days and the nightly step would start failing.
2. In the "OAuth client created" dialog, click "Download JSON".
3. /usr/bin/python3 finance/gmail_auth.py ~/Downloads/client_secret_....json
   The client id/secret are saved to .env as GMAIL_CLIENT_ID /
   GMAIL_CLIENT_SECRET (or set those by hand and run without the path), and
   the refresh token as GMAIL_REFRESH_TOKEN. Secrets are never printed, so
   they do not end up in terminal scrollback.
4. Copy GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET and GMAIL_REFRESH_TOKEN to the
   Railway service's Variables.
"""

import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import urllib.parse
import webbrowser
from pathlib import Path

import requests
from dotenv import load_dotenv, set_key

from gmail_client import API_BASE, SCOPE, TOKEN_URL

AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
ENV_PATH = Path(__file__).resolve().parent.parent / '.env'


def callback_server(expected_state: str, result: dict) -> http.server.HTTPServer:
    """Local server for Google's redirect, on any free port: Desktop app
    clients accept every loopback port, so none needs to be reserved."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if query.get('state', [None])[0] != expected_state:
                result['error'] = 'state mismatch'
            elif 'error' in query:
                result['error'] = query['error'][0]
            else:
                result['code'] = query.get('code', [None])[0]
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write(b'Done - you can close this tab and go back to the terminal.')

        def log_message(self, *args):
            pass

    return http.server.HTTPServer(('127.0.0.1', 0), Handler)


def save_client_from_json(path: str):
    """Copy client id/secret from Google's downloaded client_secret_*.json into .env."""
    try:
        info = json.loads(Path(path).expanduser().read_text())
    except (OSError, ValueError) as e:
        sys.exit(f"Could not read {path}: {e}")
    if 'installed' not in info:
        sys.exit(f"{path} is not a Desktop app client"
                 f"{' (it is a Web application client)' if 'web' in info else ''}. "
                 "Create an OAuth client ID of type 'Desktop app' and download its JSON.")
    set_key(str(ENV_PATH), 'GMAIL_CLIENT_ID', info['installed']['client_id'])
    set_key(str(ENV_PATH), 'GMAIL_CLIENT_SECRET', info['installed']['client_secret'])
    print(f"Saved GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET to {ENV_PATH}.")


def main():
    if len(sys.argv) > 1:
        save_client_from_json(sys.argv[1])
    load_dotenv(ENV_PATH)
    client_id = os.getenv('GMAIL_CLIENT_ID')
    client_secret = os.getenv('GMAIL_CLIENT_SECRET')
    if not client_id or not client_secret:
        sys.exit("No OAuth client yet. Create one of type 'Desktop app' in Google Cloud "
                 "(see this file's docstring), download its JSON, and run:\n"
                 "  /usr/bin/python3 finance/gmail_auth.py ~/Downloads/client_secret_....json")

    state = secrets.token_urlsafe(16)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    result = {}
    server = callback_server(state, result)
    redirect_uri = f'http://127.0.0.1:{server.server_address[1]}/'

    url = AUTH_URL + '?' + urllib.parse.urlencode({
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': SCOPE,
        'access_type': 'offline',
        'prompt': 'consent',  # always issue a refresh token, even on re-authorization
        'state': state,
        'code_challenge': challenge,
        'code_challenge_method': 'S256',
    })
    print("Opening the Google consent page. If no browser opens, visit:\n")
    print(url + "\n")
    webbrowser.open(url)

    while not result:
        server.handle_request()
    server.server_close()
    if 'error' in result or not result.get('code'):
        sys.exit(f"Authorization failed: {result.get('error', 'no code returned')}")

    resp = requests.post(TOKEN_URL, data={
        'code': result['code'],
        'client_id': client_id,
        'client_secret': client_secret,
        'redirect_uri': redirect_uri,
        'grant_type': 'authorization_code',
        'code_verifier': verifier,
    }, timeout=30)
    if resp.status_code != 200:
        sys.exit(f"Token exchange failed: HTTP {resp.status_code} {resp.text[:300]}")
    tokens = resp.json()
    if 'refresh_token' not in tokens:
        sys.exit("Google returned no refresh token. Remove the app's access at "
                 "https://myaccount.google.com/permissions and run this again.")

    profile = requests.get(API_BASE + '/profile', timeout=30,
                           headers={'Authorization': f"Bearer {tokens['access_token']}"})
    mailbox = profile.json().get('emailAddress', '?') if profile.ok else '?'

    set_key(str(ENV_PATH), 'GMAIL_REFRESH_TOKEN', tokens['refresh_token'])
    print(f"Authorized read-only access to {mailbox}.")
    print(f"GMAIL_REFRESH_TOKEN saved to {ENV_PATH}.")
    print("Now copy GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET and GMAIL_REFRESH_TOKEN to Railway's Variables.")


if __name__ == '__main__':
    main()

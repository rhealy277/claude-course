#!/usr/bin/env python3
"""
One-time Google OAuth setup script.

Prerequisites:
1. Go to https://console.cloud.google.com/
2. Create a project (or use an existing one)
3. Enable the Gmail API and Google Calendar API
4. Go to APIs & Services > Credentials
5. Create an OAuth 2.0 Client ID (Desktop app type)
6. Download the JSON file and save it as 'credentials.json' in this directory

Then run this script:
    python setup_google.py

It will open a browser window for you to authorize access.
After authorization, a token.json file will be created that the app uses
for automated access (no browser needed again until the token expires).
"""

import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import Config


def main():
    print("=" * 50)
    print("  Google OAuth Setup")
    print("=" * 50)
    print()

    creds_path = Config.GOOGLE_CREDENTIALS_PATH
    token_path = Config.GOOGLE_TOKEN_PATH

    if not os.path.exists(creds_path):
        print(f"ERROR: credentials.json not found at: {creds_path}")
        print()
        print("To get this file:")
        print("  1. Go to https://console.cloud.google.com/")
        print("  2. Create or select a project")
        print("  3. Enable the Gmail API and Google Calendar API")
        print("  4. Go to APIs & Services > Credentials")
        print("  5. Create an OAuth 2.0 Client ID (type: Desktop app)")
        print("  6. Download the JSON and save as 'credentials.json' here")
        sys.exit(1)

    if os.path.exists(token_path):
        print(f"Token file already exists at: {token_path}")
        response = input("Re-authorize? (y/N): ").strip().lower()
        if response != "y":
            print("Keeping existing token.")
            return
        os.remove(token_path)

    print("Opening browser for Google authorization...")
    print("Please sign in and grant access to Gmail and Calendar.")
    print()

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(
        creds_path, Config.GOOGLE_SCOPES
    )
    creds = flow.run_local_server(port=0)

    with open(token_path, "w") as f:
        f.write(creds.to_json())

    print()
    print(f"Authorization successful! Token saved to: {token_path}")
    print("You can now run 'python main.py' to start the tracker.")


if __name__ == "__main__":
    main()

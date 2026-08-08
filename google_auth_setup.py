"""
google_auth_setup.py — Phase 6 item 4: run this ONCE, by hand, to (re)connect your Google
account for Gmail (read + draft), Calendar, and Drive. Successor to auth_setup.py (Phase
1's Gmail-readonly-only version) — same InstalledAppFlow, opens a real browser window for
YOU to sign in and approve access. This script never runs itself automatically and no tool
in this codebase triggers it either — granting OAuth consent is something only you can do,
same as any other "accept this permission grant" action.

Your existing token.json (if you have one from Phase 1) only ever covered gmail.readonly —
it can't be silently upgraded to cover Calendar/Drive/Gmail-compose, Google requires a
fresh consent for new scopes, which is exactly what this script does. token.json itself is
left alone; the new token is stored encrypted at data/google_token.enc (see
google_auth.py's docstring for why).

Usage:
    python google_auth_setup.py
"""

import sys
import google_auth


def main():
    print("Starting Google Authentication flow. A browser window should open —")
    print(f"you'll be asked to approve: {', '.join(s.rsplit('/', 1)[-1] for s in google_auth.SCOPES)}")
    try:
        creds = google_auth.authenticate_interactive()
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        print("Download it from Google Cloud Console (APIs & Services > Credentials) and "
              "place it at the project root as 'credentials.json', then re-run this script.")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: authentication failed: {e}")
        sys.exit(1)

    print("\nSuccess! Encrypted and saved to data/google_token.enc.")
    status = google_auth.connection_status()
    print(f"Gmail: {'connected' if status['gmail'] else 'NOT connected'}")
    print(f"Calendar: {'connected' if status['calendar'] else 'NOT connected'}")
    print(f"Drive: {'connected' if status['drive'] else 'NOT connected'}")


if __name__ == "__main__":
    main()

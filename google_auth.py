"""
google_auth.py — Phase 6 item 4: unified Google OAuth for Gmail/Calendar/Drive, replacing
Phase 1's single-scope (gmail.readonly), plain-JSON token.json setup.

Two things changed from Phase 1, both confirmed with Devin before building:

1. Narrowest scopes that cover what's actually being asked for, not blanket account
   access:
     - gmail.readonly + gmail.compose (draft creation/reading — note honestly: Google's
       own gmail.compose scope also technically permits sending via the API, it isn't
       split any narrower than that. The "send always requires Devin's review" guarantee
       is enforced by Jarvis's own Tier-4 gate on send_email, exactly the same posture as
       every other Tier-4 tool in this project — the OAuth scope is defense-in-depth
       against a stolen token, not the mechanism that actually stops autonomous sending.)
     - calendar.events (event read/write only — not calendar.settings, no access to
       calendar sharing/settings)
     - drive.file (Google's own narrowest Drive scope — only files this app created or
       Devin explicitly opened with it, not blanket Drive read)

2. Token storage: encrypted at rest (Fernet) rather than plain JSON in the project root.
   Key lives in .env (GOOGLE_TOKEN_ENCRYPTION_KEY, generated once on first use if
   missing) — same "secrets live in .env" convention every other credential in this
   project already follows. The encrypted blob lives in data/google_token.enc, not the
   project root — data/ is already the convention for anything that isn't source, and is
   already covered by backup.py.

Migration note: Phase 1's plain token.json only ever had gmail.readonly — it can't satisfy
the new scopes on its own, so first use here triggers a real re-auth through the same
InstalledAppFlow either way. The old token.json is left alone (not deleted) — cleaning it
up is a one-line manual step for Devin, not something this module does unprompted to a
file it didn't create as part of *this* run.
"""

import os
import json

# Real bug found live-testing this module standalone: without this, a fresh Python
# process that imports google_auth.py before anything else has called load_dotenv()
# (llm.py does, at its own import time, but nothing guarantees import order) reads
# GOOGLE_TOKEN_ENCRYPTION_KEY as unset, generates a NEW random key, and silently fails to
# decrypt anything encrypted by a previous process's key — a real, reproducible data-loss
# bug, not a hypothetical one. Every other module that reads .env-backed secrets in this
# project either gets load_dotenv() for free via llm.py already having run first, or (like
# here) needs its own call — matching main.py's explicit "load .env before anything reads
# it" fix from Phase 2c's own bug history.
from dotenv import load_dotenv
load_dotenv()

DATA_DIR = "data"
TOKEN_PATH = os.path.join(DATA_DIR, "google_token.enc")
CREDENTIALS_PATH = "credentials.json"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/drive.file",
]


def _fernet():
    # Phase 8: moved to the shared credential_keys.py helper after the exact "duplicate
    # key" bug documented in .env's own comment history (three GOOGLE_TOKEN_ENCRYPTION_KEY
    # lines had accumulated from server.py's long-running process racing a separate
    # short-lived one) hit firewall_opnsense.py and nas_ugreen.py again, independently,
    # before anyone connected the three incidents — see credential_keys.py's module
    # docstring for the actual root cause and fix.
    from cryptography.fernet import Fernet
    from credential_keys import get_or_create_encryption_key
    key = get_or_create_encryption_key(
        "GOOGLE_TOKEN_ENCRYPTION_KEY", "encrypts data/google_token.enc"
    )
    return Fernet(key)


def save_credentials(creds):
    """Encrypts and writes `creds` (a google.oauth2.credentials.Credentials) to
    data/google_token.enc."""
    os.makedirs(DATA_DIR, exist_ok=True)
    plain = creds.to_json().encode("utf-8")
    encrypted = _fernet().encrypt(plain)
    with open(TOKEN_PATH, "wb") as f:
        f.write(encrypted)


def load_credentials():
    """Returns a valid Credentials object, refreshing (and re-saving, still encrypted) if
    expired, or None if no token has been saved yet or it can't be decrypted/refreshed."""
    if not os.path.exists(TOKEN_PATH):
        return None
    try:
        with open(TOKEN_PATH, "rb") as f:
            encrypted = f.read()
        plain = _fernet().decrypt(encrypted)
        from google.oauth2.credentials import Credentials
        # Real bug found live-testing this against Devin's actual (Phase 1, gmail.readonly-
        # only) token: passing the full, current SCOPES list here — rather than what the
        # stored token was ACTUALLY granted — makes the library request a refresh covering
        # scopes the refresh_token was never consented for. Google correctly rejects that
        # with invalid_scope. A token can only ever be refreshed for what it was granted;
        # reconstruct from its own stored scopes, not this module's current wishlist.
        info = json.loads(plain)
        creds = Credentials.from_authorized_user_info(info, info.get("scopes"))
    except Exception:
        return None

    if creds and creds.expired and creds.refresh_token:
        try:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
            save_credentials(creds)
        except Exception:
            return None
    return creds if creds and creds.valid else None


def authenticate_interactive():
    """Runs the real InstalledAppFlow (opens a browser) — same interactive flow
    auth_setup.py's Phase 1 version used, now requesting all the scopes above at once and
    saving encrypted. Returns the new Credentials, or raises if credentials.json (the
    OAuth client secret, not the token) is missing."""
    if not os.path.exists(CREDENTIALS_PATH):
        raise FileNotFoundError(
            f"'{CREDENTIALS_PATH}' not found — download it from Google Cloud Console first."
        )
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
    creds = flow.run_local_server(port=0)
    save_credentials(creds)
    return creds


def get_credentials():
    """The one function google_tools.py actually calls: returns valid credentials,
    refreshing silently if possible. Does NOT trigger the interactive browser flow — that
    only ever happens through the explicit connect_google_account tool, never silently
    mid-conversation."""
    return load_credentials()


def connection_status() -> dict:
    """Tier 1 — per-service connected/disconnected, for the dashboard (Phase 6 item 5) and
    directly askable in chat. A single token backs all three services (one OAuth grant,
    scoped per-service), so "connected" means the token is valid AND actually carries that
    service's scope — a token that predates a scope being added (e.g. the old Phase 1
    gmail-readonly-only token) correctly shows the newer services as disconnected rather
    than assuming a blanket "connected"."""
    creds = load_credentials()
    if creds is None:
        return {"connected": False, "gmail": False, "calendar": False, "drive": False}

    granted = set(creds.scopes or [])
    return {
        "connected": True,
        "gmail": "https://www.googleapis.com/auth/gmail.readonly" in granted,
        # Real bug found live-testing this against Devin's actual (readonly-only) token:
        # draft_email/send_email need the COMPOSE scope specifically, not just general
        # Gmail connectivity — a coarse "gmail: true" check let them fall through to a
        # raw 403 from the API instead of the same clean pre-check every other
        # insufficient-scope case gets. Exposed as its own flag so google_tools.py can
        # check the scope it actually needs, not a scope that happens to overlap.
        "gmail_compose": "https://www.googleapis.com/auth/gmail.compose" in granted,
        "calendar": "https://www.googleapis.com/auth/calendar.events" in granted,
        "drive": "https://www.googleapis.com/auth/drive.file" in granted,
        "granted_scopes": sorted(granted),
    }

"""
firewall_opnsense.py — Phase 8, section 1: OPNsense credential storage + raw API client.

Credentials stored the exact same way as the Google tokens (Devin's own explicit
instruction) — see google_auth.py's module docstring for the original reasoning. Same
shape here: Fernet-encrypted blob in data/, key generated once and persisted in .env,
never plain text on disk.

OPNsense-specific differences from the Google flow:
  - No OAuth/browser consent dance — OPNsense's own auth is a static API key/secret pair,
    generated once in its web UI (System -> Access -> API Keys) and handed to Jarvis
    directly, so there's no "authenticate_interactive()"/refresh cycle to build — just
    save/load the pair, encrypted.
  - The base URL (https://<opnsense-lan-or-twingate-address>) isn't secret on its own, but
    it's stored alongside the credentials rather than hardcoded, since it's specific to
    Devin's own network and could change (a new IP, a Twingate-fronted hostname instead of
    a bare LAN IP, etc.).
  - Every real request goes through _request(), which centralizes the
    verify=OPNSENSE_VERIFY_TLS setting (OPNsense's default self-signed cert means TLS
    verification needs to be explicitly opt-in once Devin has a real cert, not silently
    disabled) and a short timeout — no request from here should ever hang indefinitely.

This module never decides *whether* a request is safe to make — that's twingate_status.py's
job (require_twingate_or_refuse(), called by every tool in firewall_tools.py before it
reaches this module) and each Tier's own approval gate (coordinator.py). This module is
just "how to actually talk to OPNsense once we've already decided to."
"""

import os
import json

from dotenv import load_dotenv
load_dotenv()

DATA_DIR = "data"
CREDENTIALS_PATH = os.path.join(DATA_DIR, "opnsense_credentials.enc")

# OPNsense ships with a self-signed cert out of the box; Devin may or may not have put a
# real one on it yet. Defaults to verifying (fail loud on a bad cert, not silently trust
# anything) — set OPNSENSE_VERIFY_TLS=false in .env only once Devin's confirmed why
# verification is failing (self-signed cert on a LAN-only device is a normal, low-risk
# reason to turn this off deliberately; silently defaulting to off is not).
OPNSENSE_VERIFY_TLS = os.getenv("OPNSENSE_VERIFY_TLS", "true").strip().lower() != "false"
REQUEST_TIMEOUT_S = 10


def _fernet():
    from cryptography.fernet import Fernet
    from credential_keys import get_or_create_encryption_key
    key = get_or_create_encryption_key(
        "OPNSENSE_TOKEN_ENCRYPTION_KEY", "encrypts data/opnsense_credentials.enc"
    )
    return Fernet(key)


def save_credentials(base_url: str, api_key: str, api_secret: str):
    """Encrypts and writes {base_url, api_key, api_secret} to data/opnsense_credentials.enc.
    base_url is stored inside the encrypted blob too (not just alongside in plain text) —
    it's not secret on its own, but there's no benefit to splitting it out, and keeping
    everything OPNsense-related in one encrypted file is simpler than two files with two
    different trust levels to reason about."""
    os.makedirs(DATA_DIR, exist_ok=True)
    payload = json.dumps({
        "base_url": base_url.rstrip("/"), "api_key": api_key, "api_secret": api_secret,
    }).encode("utf-8")
    encrypted = _fernet().encrypt(payload)
    with open(CREDENTIALS_PATH, "wb") as f:
        f.write(encrypted)


def load_credentials():
    """Returns {"base_url", "api_key", "api_secret"} or None if nothing's saved yet, or the
    file can't be decrypted (wrong/rotated key, corruption) — never raises, matching
    google_auth.load_credentials()'s own contract."""
    if not os.path.exists(CREDENTIALS_PATH):
        return None
    try:
        with open(CREDENTIALS_PATH, "rb") as f:
            encrypted = f.read()
        plain = _fernet().decrypt(encrypted)
        return json.loads(plain)
    except Exception:
        return None


def connection_status() -> dict:
    """Tier 1 — for the GUI's integration status panel (Phase 8, section 6) and directly
    askable in chat. Deliberately does NOT make a live API call — that's a separate,
    slightly more expensive check (see is_reachable() below); this just answers "do we have
    something saved to try."""
    creds = load_credentials()
    return {
        "configured": creds is not None,
        "base_url": creds.get("base_url") if creds else None,
    }


def is_reachable() -> bool:
    """A cheap, real API call (system status) to confirm the saved credentials actually
    work right now — used by the live status panel to distinguish "configured but the API
    key was revoked" / "configured but OPNsense is unreachable" from "actually working."
    Never raises; False covers every failure mode (not configured, network error, bad
    creds, non-200)."""
    creds = load_credentials()
    if creds is None:
        return False
    try:
        resp = _request(creds, "GET", "/api/core/firmware/status")
        return resp is not None and resp.status_code == 200
    except Exception:
        return False


def _request(creds: dict, method: str, path: str, **kwargs):
    """Every real call to OPNsense's REST API goes through here — centralizes auth
    (HTTP Basic, API key as username / API secret as password, exactly how OPNsense's own
    docs describe it), TLS verification, and the timeout. Returns the raw `requests`
    Response (callers parse .json() themselves — this module doesn't know what any
    individual endpoint's payload shape should be, firewall_tools.py does) or raises the
    underlying `requests` exception, which every firewall_tools.py function catches and
    turns into the same "clearly explain what happened, don't crash the turn" shape every
    other tool in this project already uses."""
    import requests
    url = creds["base_url"] + path
    kwargs.setdefault("timeout", REQUEST_TIMEOUT_S)
    kwargs.setdefault("verify", OPNSENSE_VERIFY_TLS)
    return requests.request(
        method, url, auth=(creds["api_key"], creds["api_secret"]), **kwargs
    )

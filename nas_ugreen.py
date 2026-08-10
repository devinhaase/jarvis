"""
nas_ugreen.py — Phase 8, section 2: UGREEN NAS (UGOS) credential storage + raw API client.

Credentials stored the same way as the Google tokens (Devin's own instruction) — Fernet-
encrypted blob in data/, key generated once and persisted in .env. Unlike OPNsense's static
API key/secret, UGOS has no API-key concept at all — the only credential is Devin's own
NAS username/password, so that's what's encrypted at rest here, never written in plain
text anywhere.

**Real, accepted risk, stated plainly rather than glossed over**: UGOS has no official API
documentation. Everything below (endpoint paths, the RSA-encrypted login handshake, the
token-expiry error code) is reverse-engineered — sourced from a real, working open-source
integration (Tom-Bom-badil/home-assistant_ugreen-nas on GitHub, MIT-style community
project, actively used against real UGOS devices) rather than guessed from scratch, but it
is still unofficial. A UGOS firmware update could change this shape with zero warning and
no vendor changelog to catch it — every function here is written to fail cleanly (a clear
error, never a crash) rather than assume the shape is permanently stable, and this needs
re-verification against Devin's actual NAS once real credentials are saved.

Login handshake (from the reference implementation, reproduced here):
  1. POST {base_url}/ugreen/v1/verify/check?token= with {"username": username} — the
     response's "x-rsa-token" header carries an RSA public key (DER, base64-encoded; PEM as
     a fallback) generated fresh for this login attempt.
  2. Encrypt the password with that public key (PKCS#1 v1.5 padding, via the `cryptography`
     package already a project dependency — no new library needed), base64-encode the
     ciphertext.
  3. POST {base_url}/ugreen/v1/verify/login with {"is_simple": True, "keepalive": True,
     "otp": False, "username": username, "password": <encrypted>} — success is
     response["code"] == 200, the session token is response["data"]["token"].
  4. Every subsequent request attaches ?token=<token> (or &token=<token> if the URL already
     has a query string) — not a header.
  5. A response with "code" == 1024 means the token expired — re-login once and retry the
     same request exactly once (never loop indefinitely on repeated 1024s, which would just
     be hammering a NAS that's rejecting us for some other reason).
"""

import base64
import json
import os

from dotenv import load_dotenv
load_dotenv()

DATA_DIR = "data"
CREDENTIALS_PATH = os.path.join(DATA_DIR, "nas_ugreen_credentials.enc")
REQUEST_TIMEOUT_S = 10

# In-memory only — the session token is short-lived and cheap to re-obtain (one extra RSA
# handshake), so there's no benefit to persisting it to disk the way the long-lived
# encrypted username/password credential is. Losing this on a process restart just means
# the next call re-logs-in once, transparently.
_session_token = None


def _get_or_create_key() -> bytes:
    key = os.getenv("NAS_UGREEN_TOKEN_ENCRYPTION_KEY")
    if key:
        return key.encode()

    from cryptography.fernet import Fernet
    new_key = Fernet.generate_key()
    with open(".env", "a", encoding="utf-8") as f:
        f.write("\n# Auto-generated (Phase 8) — encrypts data/nas_ugreen_credentials.enc\n")
        f.write(f"NAS_UGREEN_TOKEN_ENCRYPTION_KEY={new_key.decode()}\n")
    os.environ["NAS_UGREEN_TOKEN_ENCRYPTION_KEY"] = new_key.decode()
    return new_key


def _fernet():
    from cryptography.fernet import Fernet
    return Fernet(_get_or_create_key())


def save_credentials(base_url: str, username: str, password: str):
    """base_url example: 'https://192.168.1.50:9443' (whatever address/port reaches the NAS
    — LAN IP or a Twingate-fronted address, same convention as OPNsense's base_url)."""
    os.makedirs(DATA_DIR, exist_ok=True)
    payload = json.dumps({
        "base_url": base_url.rstrip("/"), "username": username, "password": password,
    }).encode("utf-8")
    encrypted = _fernet().encrypt(payload)
    with open(CREDENTIALS_PATH, "wb") as f:
        f.write(encrypted)
    global _session_token
    _session_token = None  # force a fresh login on next call, not a stale token for old creds


def load_credentials():
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
    """Tier 1 — same shape convention as firewall_opnsense.connection_status(): whether
    credentials are saved, not a live reachability check (see is_reachable())."""
    creds = load_credentials()
    return {
        "configured": creds is not None,
        "base_url": creds.get("base_url") if creds else None,
    }


def is_reachable() -> bool:
    """A real login attempt, to confirm the saved credentials actually work right now.
    Never raises."""
    creds = load_credentials()
    if creds is None:
        return False
    try:
        return _login(creds) is not None
    except Exception:
        return False


def _login(creds: dict):
    """Performs the full RSA-handshake login described in the module docstring. Returns the
    session token (str) on success, None on any failure — never raises past this function,
    so callers don't need their own try/except just to call it."""
    import requests
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    base_url = creds["base_url"]
    try:
        check_resp = requests.post(
            f"{base_url}/ugreen/v1/verify/check?token=",
            json={"username": creds["username"]},
            timeout=REQUEST_TIMEOUT_S, verify=True,
        )
        rsa_header = check_resp.headers.get("x-rsa-token")
        if not rsa_header:
            return None

        try:
            pub_bytes = base64.b64decode(rsa_header)
            pub_key = serialization.load_der_public_key(pub_bytes)
        except Exception:
            pub_key = serialization.load_pem_public_key(rsa_header.encode("utf-8"))

        encrypted_password = base64.b64encode(
            pub_key.encrypt(creds["password"].encode("utf-8"), padding.PKCS1v15())
        ).decode("ascii")

        login_resp = requests.post(
            f"{base_url}/ugreen/v1/verify/login",
            json={
                "is_simple": True, "keepalive": True, "otp": False,
                "username": creds["username"], "password": encrypted_password,
            },
            timeout=REQUEST_TIMEOUT_S, verify=True,
        )
        data = login_resp.json()
        if data.get("code") != 200:
            return None
        token = (data.get("data") or {}).get("token")
        if token:
            global _session_token
            _session_token = token
        return token
    except Exception:
        return None


def _request(creds: dict, method: str, path: str, **kwargs):
    """Every real call to UGOS's API goes through here. Attaches the current session token
    as a query param (not a header — matches the reference implementation), logs in fresh
    if there's no cached token yet, and retries exactly once on a 1024 (token-expired)
    response after a fresh login — never loops indefinitely. Returns the parsed JSON body
    dict, or raises on a genuine request failure (network error, non-1024 error code) —
    callers in nas_tools.py catch this the same way firewall_tools.py catches
    firewall_opnsense._request()'s exceptions."""
    import requests

    global _session_token
    if _session_token is None:
        _session_token = _login(creds)
        if _session_token is None:
            raise RuntimeError("UGOS login failed — check the saved credentials are still correct.")

    def _do():
        sep = "&" if "?" in path else "?"
        url = f"{creds['base_url']}{path}{sep}token={_session_token}"
        return requests.request(method, url, timeout=REQUEST_TIMEOUT_S, verify=True, **kwargs)

    resp = _do()
    resp.raise_for_status()
    data = resp.json()

    if data.get("code") == 1024:
        _session_token = _login(creds)
        if _session_token is None:
            raise RuntimeError("UGOS session expired and re-login failed — check the saved credentials are still correct.")
        resp = _do()
        resp.raise_for_status()
        data = resp.json()

    return data

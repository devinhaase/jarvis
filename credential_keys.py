"""
credential_keys.py — shared "generate once, persist in .env forever" helper for every
module that encrypts a local credential file at rest (google_auth.py, firewall_opnsense.py,
nas_ugreen.py, and any future one that follows the same pattern).

Real, live bug this exists to fix: the original per-module version of this function
(still visible in each module's own commit history) checked `os.getenv(ENV_VAR)` — this
process's own in-memory environment, populated once by `load_dotenv()` at import time.
That's fine for a short-lived one-off script, but server.py is a long-running process that
imports these modules at startup, long before a credential is ever saved. When a separate,
later process (a one-off `python -c` command, or Claude Code itself) saves real credentials
for the first time, it correctly generates a key and appends it to .env — but the
already-running server process never re-reads that file, so its own next call to the same
"get or create" function still sees nothing in `os.getenv(...)`, generates a SECOND key, and
appends a duplicate. python-dotenv resolves the *last* matching line on any fresh load, so
every subsequent process reads the wrong (second) key against a file encrypted with the
first — every decrypt silently fails, reported as "not configured" even though a real,
working credential file exists on disk.

Confirmed happening in practice, not hypothetically: this exact failure hit both
firewall_opnsense.py's OPNsense credentials and nas_ugreen.py's NAS credentials in the same
session, and — per a comment already sitting in .env from an earlier session — hit
google_auth.py's Google token before that, three separate times independently before anyone
connected the dots. All three now share this one fix instead of three copies of the same
bug.

Fix: re-read the .env FILE directly (not this process's own possibly-stale os.environ)
before deciding whether to generate a new key. Any process — long-running or one-off —
always finds a key another process already wrote, so the race can't happen again.
"""

import os


def get_or_create_encryption_key(env_var: str, comment: str) -> bytes:
    """Returns the Fernet key for `env_var`, generating and persisting one to .env if it
    doesn't exist yet anywhere — on disk, checked fresh every call, not from this process's
    own environment snapshot. `comment` is a short description written above the new line
    (e.g. "encrypts data/nas_ugreen_credentials.enc") for anyone reading .env later."""
    env_path = ".env"
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(f"{env_var}="):
                    key = line.split("=", 1)[1].strip()
                    if key:
                        os.environ[env_var] = key  # keep this process's own env in sync too
                        return key.encode()

    from cryptography.fernet import Fernet
    new_key = Fernet.generate_key()
    with open(env_path, "a", encoding="utf-8") as f:
        f.write(f"\n# Auto-generated — {comment}\n")
        f.write(f"{env_var}={new_key.decode()}\n")
    os.environ[env_var] = new_key.decode()
    return new_key

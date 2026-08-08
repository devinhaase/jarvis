"""
Tests for Phase 2e: Bitwarden credential hygiene.

Split into:
  1. Real graceful-degradation checks (bw genuinely isn't installed on this machine —
     verified this path for real, not simulated).
  2. Unit tests of _analyze_vault_items() against synthetic vault fixtures — this is the
     part that doesn't need a real bw session, and it's where the actual reuse/breach logic
     lives.
  3. Retention-policy checks — no vault data ever written to disk, no plaintext passwords
     ever appear in a returned result.

Run: python test_credential_hygiene_phase2e.py
"""

import sys
import inspect

PASS = 0
FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}  {detail}")


def section(title):
    print(f"\n=== {title} ===")


from tools import ALL_TOOLS
import security_tools as st

# ---------------------------------------------------------------------------
section("1. Tool registration")
# ---------------------------------------------------------------------------
check("check_credential_hygiene registered in ALL_TOOLS", "check_credential_hygiene" in ALL_TOOLS)
from tools import REQUIRES_CAPABILITY
check("check_credential_hygiene requires the 'filesystem' capability",
      REQUIRES_CAPABILITY.get("check_credential_hygiene") == "filesystem")


# ---------------------------------------------------------------------------
section("2. Graceful degradation — bw CLI genuinely not installed on this machine")
# ---------------------------------------------------------------------------
result = st.check_credential_hygiene()
check("returns an error dict, doesn't raise, when bw isn't installed",
      isinstance(result, dict) and "error" in result, f"got: {result}")
check("error message points at the install page",
      "bitwarden.com" in result.get("error", "").lower(), f"got: {result.get('error')}")


# ---------------------------------------------------------------------------
section("3. Graceful degradation — BW_SESSION not set (simulate bw present)")
# ---------------------------------------------------------------------------
import unittest.mock as mock

with mock.patch("shutil.which", return_value="/fake/path/bw"), \
     mock.patch.dict("os.environ", {}, clear=False):
    import os as _os
    _os.environ.pop("BW_SESSION", None)
    result = st.check_credential_hygiene()
    check("returns an error dict when BW_SESSION is unset",
          isinstance(result, dict) and "error" in result, f"got: {result}")
    check("error message explains bw login/unlock, mentions Jarvis never touches the master password",
          "master password" in result.get("error", "").lower(), f"got: {result.get('error')}")


# ---------------------------------------------------------------------------
section("4. Vault analysis — password reuse detection")
# ---------------------------------------------------------------------------
FIXTURE_ITEMS = [
    {"name": "GitHub", "login": {"password": "correct-horse-battery-staple-1"}},
    {"name": "GitLab", "login": {"password": "correct-horse-battery-staple-1"}},  # reused w/ GitHub
    {"name": "AWS Console", "login": {"password": "unique-aws-pw-xyz"}},
    {"name": "Old Forum", "login": {"password": "correct-horse-battery-staple-1"}},  # reused 3x
    {"name": "No Password Note", "login": {}},  # no password — should be skipped
    {"name": "Bank", "login": {"password": "another-unique-one"}},
]

analysis = st._analyze_vault_items(FIXTURE_ITEMS, max_breach_checks=0)  # skip network calls here

check("counts only items that actually have a password", analysis["total_login_items"] == 5,
      f"got: {analysis['total_login_items']}")
check("dedupes to the correct number of unique passwords", analysis["unique_passwords"] == 3,
      f"got: {analysis['unique_passwords']}")
check("finds exactly one reuse group", len(analysis["reused_password_groups"]) == 1,
      f"got: {analysis['reused_password_groups']}")

if analysis["reused_password_groups"]:
    group = analysis["reused_password_groups"][0]
    check("reuse group has the right count", group["reuse_count"] == 3, f"got: {group}")
    check("reuse group names the right items",
          set(group["item_names"]) == {"GitHub", "GitLab", "Old Forum"}, f"got: {group['item_names']}")

check("max_breach_checks=0 truncates and reports it",
      analysis["breach_check_truncated"] is True and analysis["passwords_checked_against_breach_db"] == 0,
      f"got: {analysis}")


# ---------------------------------------------------------------------------
section("5. Vault analysis — no reuse when every password is unique")
# ---------------------------------------------------------------------------
NO_REUSE_ITEMS = [
    {"name": "Site A", "login": {"password": "aaaaaaaa1"}},
    {"name": "Site B", "login": {"password": "bbbbbbbb2"}},
    {"name": "Site C", "login": {"password": "cccccccc3"}},
]
analysis2 = st._analyze_vault_items(NO_REUSE_ITEMS, max_breach_checks=0)
check("no reuse groups when all passwords are unique", analysis2["reused_password_groups"] == [],
      f"got: {analysis2['reused_password_groups']}")
check("reused_password_count is 0", analysis2["reused_password_count"] == 0)


# ---------------------------------------------------------------------------
section("6. Vault analysis — empty vault doesn't crash")
# ---------------------------------------------------------------------------
analysis3 = st._analyze_vault_items([], max_breach_checks=5)
check("empty vault returns zeroed-out result, not an error",
      analysis3["total_login_items"] == 0 and analysis3["unique_passwords"] == 0)


# ---------------------------------------------------------------------------
section("7. Retention / disclosure policy — no plaintext password ever in the result")
# ---------------------------------------------------------------------------
import json as _json
serialized = _json.dumps(analysis)
check(
    "the raw password values never appear in the returned analysis (only item names/counts)",
    "correct-horse-battery-staple-1" not in serialized and "unique-aws-pw-xyz" not in serialized,
    "a plaintext password leaked into the result"
)

src = inspect.getsource(st)
write_calls = [
    line for line in src.splitlines()
    if "open(" in line and any(m in line for m in ["'w'", '"w"', "'wb'", '"wb"'])
]
check("check_credential_hygiene / _analyze_vault_items never write to disk",
      not any("credential" in l.lower() or "vault" in l.lower() or "_analyze_vault_items" in l for l in write_calls),
      f"suspicious write calls: {write_calls}")


print(f"\n{'=' * 55}\n{PASS} passed, {FAIL} failed\n{'=' * 55}")
sys.exit(1 if FAIL else 0)

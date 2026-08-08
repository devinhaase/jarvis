"""
Tests for Phase 7: backup.py — local backup/export, and its create_backup tool registration.

Runs entirely inside an isolated temp directory (chdir'd) so it never touches the real
backups/ or data/ — this test creates its own fake data/ tree to zip up.

Run: python test_backup_phase7.py
"""

import os
import sys
import shutil
import zipfile
import tempfile

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

# ---------------------------------------------------------------------------
section("1. Tool registration")
# ---------------------------------------------------------------------------
check("create_backup registered", "create_backup" in ALL_TOOLS)
if "create_backup" in ALL_TOOLS:
    from tools import Tier
    check("create_backup is Tier 2 (reversible/logged, not destructive)",
          ALL_TOOLS["create_backup"].tier == Tier.TIER_2)
from tools import REQUIRES_CAPABILITY
check("create_backup requires 'filesystem' capability", REQUIRES_CAPABILITY.get("create_backup") == "filesystem")

# ---------------------------------------------------------------------------
section("2. create_backup / list_backups / prune, in an isolated fake project dir")
# ---------------------------------------------------------------------------
import backup as backup_module

tmp_root = tempfile.mkdtemp(prefix="jarvis_test_backup_")
cwd = os.getcwd()
try:
    os.chdir(tmp_root)

    # Fake project content to back up
    os.makedirs("data/sessions", exist_ok=True)
    with open("data/jarvis.db", 'w') as f:
        f.write("fake db content")
    with open("data/sessions/thing.json", 'w') as f:
        f.write("{}")
    with open(".env", 'w') as f:
        f.write("ACTIVE_LLM=ollama\n")
    with open("devices.json", 'w') as f:
        f.write("{}")
    # credentials.json / token.json deliberately absent — tests the "skipped" path

    result = backup_module.create_backup()
    check("create_backup returns a path that actually exists", os.path.exists(result["backup_path"]))
    check("data/, .env, devices.json all included", set(result["included"]) == {"data/", ".env", "devices.json"},
          f"got: {result['included']}")
    check("credentials.json/token.json reported as skipped (not found), not an error",
          set(result["skipped_not_found"]) == {"credentials.json", "token.json"}, f"got: {result['skipped_not_found']}")

    with zipfile.ZipFile(result["backup_path"]) as zf:
        names = zf.namelist()
        check("the zip actually contains the nested session file",
              any("sessions" in n and "thing.json" in n for n in names), f"got: {names}")
        check("the zip contains .env and devices.json too", ".env" in names and "devices.json" in names, f"got: {names}")

    listing = backup_module.list_backups()
    check("list_backups sees the one we just created", len(listing) == 1, f"got: {listing}")

    # Create several more, past the prune limit, to verify pruning actually deletes old ones
    original_limit = backup_module.MAX_BACKUPS_KEPT
    backup_module.MAX_BACKUPS_KEPT = 3
    try:
        for _ in range(4):
            backup_module.create_backup()
        listing = backup_module.list_backups()
        check("pruning keeps exactly MAX_BACKUPS_KEPT backups, not unbounded growth",
              len(listing) == 3, f"got {len(listing)} backups")
    finally:
        backup_module.MAX_BACKUPS_KEPT = original_limit

    # restore_backup requires interactive confirmation ('restore') — verify it refuses
    # without that confirmation rather than silently overwriting anything.
    import io
    import contextlib
    import unittest.mock as mock

    os.makedirs("data_restore_target", exist_ok=True)  # sentinel — should survive an aborted restore
    latest = backup_module.list_backups()[0]["path"]
    with mock.patch("builtins.input", return_value="not-the-right-word"):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            backup_module.restore_backup(latest)
        check("restore_backup refuses without the exact 'restore' confirmation",
              "cancelled" in buf.getvalue().lower(), f"got: {buf.getvalue()!r}")

finally:
    os.chdir(cwd)
    shutil.rmtree(tmp_root, ignore_errors=True)

print(f"\n{'=' * 55}\n{PASS} passed, {FAIL} failed\n{'=' * 55}")
sys.exit(1 if FAIL else 0)

"""
backup.py — Local backup/export for everything that would actually hurt to lose: the
conversation database, memory, authorized-targets list, device registry, and credentials.
Zips it all into backups/jarvis_backup_{timestamp}.zip. Local-only — nothing is uploaded
anywhere; this just protects against "this one disk dies" the same way the new git repo
protects the code itself.

Usage:
    python backup.py                  # create a backup now, prune old ones
    python backup.py --list           # list existing backups
    python backup.py --restore FILE   # restore from a backup zip (see warning below)

Restore is deliberately a manual, explicit CLI action — not an agent tool. It overwrites
current data/, which is exactly the kind of destructive-if-wrong action that shouldn't be
one LLM tool-call away; create_backup() (the non-destructive half) is registered as a tool,
restore is not.
"""

import os
import sys
import glob
import shutil
import zipfile
import argparse
from datetime import datetime

BACKUP_DIR = "backups"
MAX_BACKUPS_KEPT = 10

# Everything worth protecting — the SQLite conversation DB, memory files, authorized
# targets, and every credential file this project reads at import time. If a path doesn't
# exist (e.g. no .env yet, or Gmail was never set up), it's just skipped, not an error.
BACKUP_PATHS = ["data", ".env", "devices.json", "credentials.json", "token.json"]


def create_backup(output_dir: str = BACKUP_DIR) -> dict:
    """Create a timestamped zip backup of data/ + top-level credential files. Prunes old
    backups beyond MAX_BACKUPS_KEPT. Returns a dict describing what was backed up."""
    os.makedirs(output_dir, exist_ok=True)
    # Microsecond precision, not just seconds — two backups triggered in quick succession
    # (a double-click, a fast retry) would otherwise share one timestamp and the second
    # call would silently overwrite the first instead of creating a distinct backup.
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    zip_path = os.path.join(output_dir, f"jarvis_backup_{ts}.zip")

    included = []
    skipped = []

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for path in BACKUP_PATHS:
            if not os.path.exists(path):
                skipped.append(path)
                continue
            if os.path.isdir(path):
                for root, _dirs, files in os.walk(path):
                    for fname in files:
                        full = os.path.join(root, fname)
                        zf.write(full, arcname=full)
                included.append(path + "/")
            else:
                zf.write(path, arcname=path)
                included.append(path)

    size_bytes = os.path.getsize(zip_path)
    pruned = _prune_old_backups(output_dir)

    return {
        "backup_path": zip_path,
        "size_bytes": size_bytes,
        "included": included,
        "skipped_not_found": skipped,
        "pruned_old_backups": pruned,
    }


def _prune_old_backups(output_dir: str) -> list:
    """Keep only the MAX_BACKUPS_KEPT most recent backups — this runs unattended (a tool
    call, potentially daily), so it shouldn't silently fill the disk over months of use."""
    backups = sorted(glob.glob(os.path.join(output_dir, "jarvis_backup_*.zip")))
    excess = backups[:-MAX_BACKUPS_KEPT] if len(backups) > MAX_BACKUPS_KEPT else []
    for path in excess:
        os.remove(path)
    return excess


def list_backups(output_dir: str = BACKUP_DIR) -> list:
    backups = sorted(glob.glob(os.path.join(output_dir, "jarvis_backup_*.zip")), reverse=True)
    return [
        {"path": b, "size_bytes": os.path.getsize(b),
         "created": datetime.fromtimestamp(os.path.getmtime(b)).isoformat()}
        for b in backups
    ]


def restore_backup(zip_path: str):
    """Extracts a backup zip over the current working directory, OVERWRITING data/ and any
    credential files it contains. Manual CLI use only — see module docstring."""
    if not os.path.exists(zip_path):
        print(f"No such backup file: {zip_path}")
        return
    print(f"About to restore from {zip_path}.")
    print("This OVERWRITES your current data/ and any credential files included in the backup.")
    confirm = input("Type 'restore' to proceed: ").strip()
    if confirm != "restore":
        print("Cancelled.")
        return
    with zipfile.ZipFile(zip_path, 'r') as zf:
        zf.extractall(".")
    print("Restore complete.")


def main():
    parser = argparse.ArgumentParser(description="Jarvis local backup/restore")
    parser.add_argument("--list", action="store_true", help="List existing backups")
    parser.add_argument("--restore", metavar="FILE", help="Restore from a backup zip (manual confirmation required)")
    args = parser.parse_args()

    if args.list:
        for b in list_backups():
            size_mb = b["size_bytes"] / (1024 * 1024)
            print(f"{b['path']}  ({size_mb:.1f} MB, created {b['created']})")
        return

    if args.restore:
        restore_backup(args.restore)
        return

    result = create_backup()
    size_mb = result["size_bytes"] / (1024 * 1024)
    print(f"Backup created: {result['backup_path']} ({size_mb:.1f} MB)")
    print(f"Included: {', '.join(result['included'])}")
    if result["skipped_not_found"]:
        print(f"Skipped (not found): {', '.join(result['skipped_not_found'])}")
    if result["pruned_old_backups"]:
        print(f"Pruned {len(result['pruned_old_backups'])} old backup(s) beyond the {MAX_BACKUPS_KEPT}-backup limit")


if __name__ == "__main__":
    main()

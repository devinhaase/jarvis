"""
Tests for Phase 7: task_manager.py — local task/reminder tracking.

Uses an isolated temp TaskStore throughout — never touches the real data/tasks.db.

Run: python test_task_manager_phase7.py
"""

import os
import sys
import shutil
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
for name in ("add_task", "list_tasks", "complete_task", "delete_task"):
    check(f"{name} registered", name in ALL_TOOLS)
from tools import REQUIRES_CAPABILITY
check("task tools are NOT capability-gated (personal data, like conversations/projects, "
      "available to any connected device regardless of 'filesystem')",
      not any(t in REQUIRES_CAPABILITY for t in ("add_task", "list_tasks", "complete_task", "delete_task")))

# ---------------------------------------------------------------------------
section("2. TaskStore, isolated")
# ---------------------------------------------------------------------------
from task_manager import TaskStore

tmp_dir = tempfile.mkdtemp(prefix="jarvis_test_tasks_")
store = TaskStore(path=os.path.join(tmp_dir, "test_tasks.db"))

t1 = store.add_task("Renew domain", details="jarvis-project.dev expires soon", due_date="2026-09-01")
t2 = store.add_task("Rotate guest wifi password")
t3 = store.add_task("Patch NAS firmware", due_date="2026-08-10")

check("add_task returns distinct ids", len({t1, t2, t3}) == 3)

try:
    store.add_task("   ")
    check("blank title is rejected", False, "no exception raised")
except ValueError:
    check("blank title is rejected", True)

undone = store.list_tasks()
check("list_tasks returns all 3 undone tasks", len(undone) == 3, f"got: {len(undone)}")
check("undone tasks with a due date sort before ones without, soonest first",
      [t["id"] for t in undone if t["due_date"]] == [t3, t1],
      f"got order: {[(t['id'], t['due_date']) for t in undone]}")

check("get_task returns the right task", store.get_task(t1)["title"] == "Renew domain")
check("get_task on a bogus id returns None", store.get_task("nope") is None)

# ---------------------------------------------------------------------------
section("3. Completing and deleting")
# ---------------------------------------------------------------------------
ok = store.complete_task(t2)
check("complete_task succeeds for a real task", ok is True)
check("completed task carries a completed_at timestamp", store.get_task(t2)["completed_at"] is not None)

undone_after = store.list_tasks()
check("completed task no longer shows up in the default (undone-only) listing",
      t2 not in [t["id"] for t in undone_after], f"got: {[t['id'] for t in undone_after]}")

all_tasks = store.list_tasks(include_completed=True)
check("include_completed=True shows all 3, completed last",
      len(all_tasks) == 3 and all_tasks[-1]["id"] == t2, f"got: {[t['id'] for t in all_tasks]}")

check("completing a bogus id returns False, doesn't raise", store.complete_task("nope") is False)

ok = store.delete_task(t3)
check("delete_task succeeds for a real task", ok is True)
check("deleted task is actually gone", store.get_task(t3) is None)
check("deleting a bogus id returns False, doesn't raise", store.delete_task("nope") is False)

# ---------------------------------------------------------------------------
section("4. Tool-facing wrapper functions (what the LLM actually calls)")
# ---------------------------------------------------------------------------
import task_manager as tm

original_store = tm.store
tm.store = TaskStore(path=os.path.join(tmp_dir, "test_tasks_wrappers.db"))
try:
    result = tm.add_task("Check backup ran", details="verify last night's cron")
    check("add_task wrapper returns the full task dict", result["title"] == "Check backup ran" and "id" in result)

    listing = tm.list_tasks()
    check("list_tasks wrapper works", len(listing) == 1 and listing[0]["title"] == "Check backup ran")

    completed = tm.complete_task(result["id"])
    check("complete_task wrapper marks it done", completed["completed"] == 1)

    err = tm.complete_task("not-a-real-id")
    check("complete_task wrapper reports a clean error for a bogus id, doesn't raise", "error" in err, f"got: {err}")

    deleted = tm.delete_task(result["id"])
    check("delete_task wrapper confirms deletion", deleted["deleted"] is True)
finally:
    tm.store = original_store

shutil.rmtree(tmp_dir, ignore_errors=True)

print(f"\n{'=' * 55}\n{PASS} passed, {FAIL} failed\n{'=' * 55}")
sys.exit(1 if FAIL else 0)

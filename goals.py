"""
goals.py — standing objectives, distinct from task_manager.py's one-shot tasks. A task is
"done" in a single action ("renew the domain"); a goal is open-ended and gets periodically
revisited ("keep an eye on the Twingate reconnect issue", "get better at triaging security
posture alerts") — see the research-session entry in task.md/MEMORY.md for why this is a
separate concept rather than tasks with no due date.

Same local-only SQLite pattern as task_manager.py (data/goals.db, its own file — same
"don't contend with conversation_store.py's connection" reasoning), same
try/except-import-degrades-silently registration in tools.py.

A goal has a check_in_interval_days and a next_check_in timestamp. goals_loop() (below)
runs once a day, finds every active goal whose next_check_in has passed, and surfaces it —
posts into a dedicated "Goals" conversation asking for a status update, same shape every
other periodic loop here uses (see posture_monitor.py/network_monitor.py), and reschedules
next_check_in from whenever the check-in actually happens (check_in_goal()), not from a
fixed calendar interval — a goal nobody has looked at in three weeks gets asked about once,
not three backlogged times.
"""

import os
import sqlite3
import time
import uuid
import threading
import asyncio

from conversation_store import store as conv_store

DB_PATH = os.path.join("data", "goals.db")
MONITOR_CONV_FILE = os.path.join("data", ".goals_conversation_id")
DEFAULT_CHECK_IN_INTERVAL_DAYS = 7
DEFAULT_LOOP_INTERVAL_SECONDS = 6 * 60 * 60  # checks 4x/day for due goals; cheap, all-local

_SCHEMA = """
CREATE TABLE IF NOT EXISTS goals (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    details TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    check_in_interval_days INTEGER NOT NULL DEFAULT 7,
    created_at REAL NOT NULL,
    next_check_in REAL NOT NULL,
    resolved_at REAL
);
CREATE TABLE IF NOT EXISTS goal_checkins (
    id TEXT PRIMARY KEY,
    goal_id TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


class GoalStore:
    def __init__(self, path: str = None):
        self.path = path or DB_PATH
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def add_goal(self, title: str, details: str = "", check_in_interval_days: int = None) -> str:
        title = (title or "").strip()
        if not title:
            raise ValueError("goal title cannot be empty")
        interval = check_in_interval_days or DEFAULT_CHECK_IN_INTERVAL_DAYS
        gid = uuid.uuid4().hex
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO goals (id, title, details, status, check_in_interval_days, created_at, next_check_in) "
                "VALUES (?,?,?,'active',?,?,?)",
                (gid, title, details or "", interval, now, now + interval * 86400)
            )
            self._conn.commit()
        return gid

    def list_goals(self, include_resolved: bool = False) -> list:
        with self._lock:
            if include_resolved:
                rows = self._conn.execute(
                    "SELECT * FROM goals ORDER BY status ASC, next_check_in ASC"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM goals WHERE status='active' ORDER BY next_check_in ASC"
                ).fetchall()
        return [dict(r) for r in rows]

    def list_due_goals(self, as_of: float = None) -> list:
        as_of = as_of if as_of is not None else time.time()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM goals WHERE status='active' AND next_check_in <= ? ORDER BY next_check_in ASC",
                (as_of,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_goal(self, goal_id: str):
        with self._lock:
            row = self._conn.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
            if not row:
                return None
            checkins = self._conn.execute(
                "SELECT * FROM goal_checkins WHERE goal_id=? ORDER BY created_at DESC", (goal_id,)
            ).fetchall()
        result = dict(row)
        result["checkins"] = [dict(c) for c in checkins]
        return result

    def check_in_goal(self, goal_id: str, note: str) -> bool:
        """Logs a progress note and reschedules next_check_in from now (not from the goal's
        original schedule) — the point is "when did someone last actually look at this,"
        not a fixed calendar cadence that drifts meaningless once it's been missed."""
        with self._lock:
            row = self._conn.execute("SELECT check_in_interval_days FROM goals WHERE id=? AND status='active'", (goal_id,)).fetchone()
            if not row:
                return False
            now = time.time()
            self._conn.execute(
                "INSERT INTO goal_checkins (id, goal_id, note, created_at) VALUES (?,?,?,?)",
                (uuid.uuid4().hex, goal_id, note, now)
            )
            self._conn.execute(
                "UPDATE goals SET next_check_in=? WHERE id=?",
                (now + row["check_in_interval_days"] * 86400, goal_id)
            )
            self._conn.commit()
        return True

    def resolve_goal(self, goal_id: str, status: str) -> bool:
        assert status in ("achieved", "abandoned")
        with self._lock:
            if not self._conn.execute("SELECT 1 FROM goals WHERE id=? AND status='active'", (goal_id,)).fetchone():
                return False
            self._conn.execute(
                "UPDATE goals SET status=?, resolved_at=? WHERE id=?", (status, time.time(), goal_id)
            )
            self._conn.commit()
        return True

    def delete_goal(self, goal_id: str) -> bool:
        with self._lock:
            if not self._conn.execute("SELECT 1 FROM goals WHERE id=?", (goal_id,)).fetchone():
                return False
            self._conn.execute("DELETE FROM goals WHERE id=?", (goal_id,))
            self._conn.execute("DELETE FROM goal_checkins WHERE goal_id=?", (goal_id,))
            self._conn.commit()
        return True


store = GoalStore()


# ---------------------------------------------------------------------------
# Tool-facing wrappers — thin, mirrors task_manager.py's own wrapper section
# ---------------------------------------------------------------------------

def add_goal(title: str, details: str = "", check_in_interval_days: int = None) -> dict:
    """Add a standing goal Jarvis will periodically ask about (default: every 7 days).
    Unlike a task, a goal isn't "done" in one step — it stays active until you mark it
    achieved or abandoned, and gets resurfaced for a status update on its own schedule."""
    goal_id = store.add_goal(title, details, check_in_interval_days)
    return store.get_goal(goal_id)


def list_goals(include_resolved: bool = False) -> list:
    """List goals, soonest-check-in first — active goals by default, pass
    include_resolved=true for achieved/abandoned ones too."""
    return store.list_goals(include_resolved=include_resolved)


def check_in_goal(goal_id: str, note: str) -> dict:
    """Log a progress note against a goal and reschedule its next check-in from now."""
    ok = store.check_in_goal(goal_id, note)
    if not ok:
        return {"error": f"No active goal with id {goal_id!r}"}
    return store.get_goal(goal_id)


def resolve_goal(goal_id: str, status: str) -> dict:
    """Mark a goal 'achieved' or 'abandoned' — stops it from ever being surfaced again."""
    ok = store.resolve_goal(goal_id, status)
    if not ok:
        return {"error": f"No active goal with id {goal_id!r}"}
    return store.get_goal(goal_id)


def delete_goal(goal_id: str) -> dict:
    ok = store.delete_goal(goal_id)
    return {"deleted": ok, "goal_id": goal_id}


# ---------------------------------------------------------------------------
# Background loop — same shape as posture_monitor.py/network_monitor.py's loops, but
# surfacing overdue check-ins instead of diffing a snapshot.
# ---------------------------------------------------------------------------

def _get_or_create_goals_conversation() -> str:
    if os.path.exists(MONITOR_CONV_FILE):
        with open(MONITOR_CONV_FILE, "r") as f:
            cid = f.read().strip()
        if cid and conv_store.conversation_exists(cid):
            return cid
    cid = conv_store.create_conversation(device_id="goals-monitor", title="Goals")
    conv_store.rename_conversation(cid, "Goals")
    os.makedirs(os.path.dirname(MONITOR_CONV_FILE) or ".", exist_ok=True)
    with open(MONITOR_CONV_FILE, "w") as f:
        f.write(cid)
    return cid


async def _check_due_goals(broadcast_all):
    due = await asyncio.to_thread(store.list_due_goals)
    if not due:
        return

    lines = [f"- **{g['title']}** (last checked in {_days_ago(g)} ago) — {g['details'] or 'no details'}" for g in due]
    text = (
        "These goals are due for a check-in — how are they going?\n" + "\n".join(lines)
    )
    conv_id = _get_or_create_goals_conversation()
    conv_store.add_message(conv_id, "assistant", text, source="text")
    if broadcast_all:
        await broadcast_all({
            "type": "stream_end", "conversation_id": conv_id,
            "full_text": text, "tools_ran": [], "denied": [],
        })
        await broadcast_all({"type": "conversation_list_changed"})

    try:
        import push_notifications as _push
        headline = due[0]["title"] if len(due) == 1 else f"{len(due)} goals due for a check-in"
        await asyncio.to_thread(
            _push.send_to_all, "goal_checkin", "Jarvis: goal check-in", headline,
            tag="goals", conversation_id=conv_id,
        )
    except Exception:
        pass


def _days_ago(goal: dict) -> str:
    reference = goal.get("next_check_in", 0) - goal.get("check_in_interval_days", DEFAULT_CHECK_IN_INTERVAL_DAYS) * 86400
    days = max(0, int((time.time() - reference) / 86400))
    return f"{days}d" if days else "<1d"


async def goals_loop(broadcast_all=None, interval_seconds: int = None):
    """Runs forever until cancelled by server.py's lifespan on shutdown. Checks a few times
    a day (default every 6h) for goals whose next_check_in has passed — cheap and entirely
    local, no reason to check less often than that, and it means a goal set to check in
    daily doesn't sit until the next day's single check."""
    interval = interval_seconds or int(os.getenv("JARVIS_GOALS_LOOP_INTERVAL_SECONDS", str(DEFAULT_LOOP_INTERVAL_SECONDS)))
    while True:
        try:
            await _check_due_goals(broadcast_all)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[Goals] check failed: {e}")
        await asyncio.sleep(interval)

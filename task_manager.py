"""
task_manager.py — Local-only task/reminder tracking (data/tasks.db, its own SQLite file —
separate from conversation_store.py's jarvis.db, since tasks aren't conversation data and
this avoids two connections contending over one file).

Deliberately NOT a Google Calendar integration: that would need the user to do the same
OAuth dance already required for Gmail (credentials.json, a consent screen, scopes) before
any of this is usable. A local task list works immediately, needs no setup, and covers the
actual ask ("track things for me") without a new external dependency.
"""

import os
import sqlite3
import time
import uuid
import threading

DB_PATH = os.path.join("data", "tasks.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    details TEXT DEFAULT '',
    due_date TEXT,
    completed INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    completed_at REAL
);
"""


class TaskStore:
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

    def add_task(self, title: str, details: str = "", due_date: str = None) -> str:
        title = (title or "").strip()
        if not title:
            raise ValueError("task title cannot be empty")
        tid = uuid.uuid4().hex
        with self._lock:
            self._conn.execute(
                "INSERT INTO tasks (id, title, details, due_date, completed, created_at) "
                "VALUES (?,?,?,?,0,?)",
                (tid, title, details or "", due_date, time.time())
            )
            self._conn.commit()
        return tid

    def list_tasks(self, include_completed: bool = False) -> list:
        with self._lock:
            if include_completed:
                rows = self._conn.execute(
                    "SELECT * FROM tasks ORDER BY completed ASC, due_date IS NULL, due_date ASC, created_at ASC"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM tasks WHERE completed=0 "
                    "ORDER BY due_date IS NULL, due_date ASC, created_at ASC"
                ).fetchall()
        return [dict(r) for r in rows]

    def get_task(self, task_id: str):
        with self._lock:
            row = self._conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None

    def complete_task(self, task_id: str) -> bool:
        with self._lock:
            if not self._conn.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone():
                return False
            self._conn.execute(
                "UPDATE tasks SET completed=1, completed_at=? WHERE id=?", (time.time(), task_id)
            )
            self._conn.commit()
        return True

    def delete_task(self, task_id: str) -> bool:
        with self._lock:
            if not self._conn.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone():
                return False
            self._conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
            self._conn.commit()
        return True


store = TaskStore()


# ---------------------------------------------------------------------------
# Tool-facing wrappers — thin, so the tools registered in tools.py stay plain functions
# ---------------------------------------------------------------------------

def add_task(title: str, details: str = "", due_date: str = None) -> dict:
    """Add a task/reminder. due_date, if given, should be an ISO date string (YYYY-MM-DD)."""
    task_id = store.add_task(title, details, due_date)
    return store.get_task(task_id)


def list_tasks(include_completed: bool = False) -> list:
    """List tasks, soonest-due first, undone tasks by default."""
    return store.list_tasks(include_completed=include_completed)


def complete_task(task_id: str) -> dict:
    ok = store.complete_task(task_id)
    if not ok:
        return {"error": f"No task with id {task_id!r}"}
    return store.get_task(task_id)


def delete_task(task_id: str) -> dict:
    ok = store.delete_task(task_id)
    return {"deleted": ok, "task_id": task_id}

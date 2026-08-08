"""
conversation_store.py — Central, persistent chat history for Jarvis (Phase 3a).

Replaces the Phase 2d flat data/sessions/{id}.json files with one SQLite database
(data/jarvis.db), so conversations survive across devices, can be listed / renamed /
deleted / searched, and get an auto-generated title — the things a normal chat app
needs that per-session JSON files were never going to give us.

One conversation = one row in `conversations` + N rows in `messages`. A lightweight
FTS5 virtual table mirrors messages.content for full-text search across everything
ever said, regardless of which device or input method (typed or voice) produced it.

Deliberately dependency-free of llm.py / session_manager.py — this module only knows
how to persist and query conversations, not how to talk to a model. Auto-titling is
driven from server.py, which has the LLM.
"""

import os
import sqlite3
import time
import uuid
import json
import threading

DB_PATH = os.path.join("data", "jarvis.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'New conversation',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    last_device TEXT DEFAULT '',
    titled INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'text',
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content, content='messages', content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY,
    conversation_id TEXT,
    project_id TEXT,
    filename TEXT NOT NULL,
    content_text TEXT,
    extractable INTEGER NOT NULL DEFAULT 0,
    truncated INTEGER NOT NULL DEFAULT 0,
    size_bytes INTEGER NOT NULL,
    uploaded_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_files_conv ON files(conversation_id);
CREATE INDEX IF NOT EXISTS idx_files_project ON files(project_id);
"""


def _sanitize_fts_query(query: str) -> str:
    """Turn free-typed user search text into a safe FTS5 MATCH expression — each word
    becomes a quoted literal phrase (AND-ed together by FTS5's default), so stray
    characters like '-', '*', '"' in what someone typed can't be parsed as FTS5 query
    syntax and blow up the search instead of just... searching for what they typed."""
    terms = query.split()
    escaped = ['"' + t.replace('"', '""') + '"' for t in terms if t]
    return " ".join(escaped) if escaped else '""'


def _ensure_column(conn, table: str, column: str, coltype: str):
    """Idempotent ALTER TABLE ADD COLUMN — SQLite has no 'IF NOT EXISTS' for columns, and
    projects (added after conversations already existed on real installs) needs one added
    to an existing table rather than created fresh, so this can't just live in _SCHEMA."""
    cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


class ConversationStore:
    def __init__(self, path: str = None):
        self.path = path or DB_PATH
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        _ensure_column(self._conn, "conversations", "project_id", "TEXT")
        self._conn.commit()
        self._migrate_legacy_sessions()

    # -------------------------------------------------------------- conversations --
    def create_conversation(self, device_id: str = "", title: str = "New conversation") -> str:
        cid = uuid.uuid4().hex
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO conversations (id, title, created_at, updated_at, last_device, titled) "
                "VALUES (?,?,?,?,?,0)",
                (cid, title, now, now, device_id)
            )
            self._conn.commit()
        return cid

    def conversation_exists(self, conversation_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM conversations WHERE id=?", (conversation_id,)
            ).fetchone()
        return row is not None

    def list_conversations(self, limit: int = 200, project_id: str = None) -> list:
        with self._lock:
            if project_id is not None:
                rows = self._conn.execute(
                    "SELECT id, title, created_at, updated_at, project_id FROM conversations "
                    "WHERE project_id=? ORDER BY updated_at DESC LIMIT ?",
                    (project_id, limit)
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT id, title, created_at, updated_at, project_id FROM conversations "
                    "ORDER BY updated_at DESC LIMIT ?",
                    (limit,)
                ).fetchall()
        return [dict(r) for r in rows]

    def set_conversation_project(self, conversation_id: str, project_id: str = None):
        with self._lock:
            self._conn.execute(
                "UPDATE conversations SET project_id=? WHERE id=?", (project_id, conversation_id)
            )
            self._conn.commit()

    def rename_conversation(self, conversation_id: str, title: str):
        title = (title or "").strip()[:120] or "Untitled"
        with self._lock:
            self._conn.execute(
                "UPDATE conversations SET title=?, titled=1 WHERE id=?", (title, conversation_id)
            )
            self._conn.commit()

    def delete_conversation(self, conversation_id: str):
        with self._lock:
            self._conn.execute("DELETE FROM messages WHERE conversation_id=?", (conversation_id,))
            self._conn.execute("DELETE FROM files WHERE conversation_id=?", (conversation_id,))
            self._conn.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
            self._conn.commit()

    def touch(self, conversation_id: str, device_id: str = ""):
        with self._lock:
            self._conn.execute(
                "UPDATE conversations SET updated_at=?, last_device=? WHERE id=?",
                (time.time(), device_id, conversation_id)
            )
            self._conn.commit()

    def is_titled(self, conversation_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT titled FROM conversations WHERE id=?", (conversation_id,)
            ).fetchone()
        return bool(row and row["titled"])

    def get_conversation(self, conversation_id: str):
        with self._lock:
            row = self._conn.execute(
                "SELECT id, title, created_at, updated_at, project_id FROM conversations WHERE id=?",
                (conversation_id,)
            ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------ messages --
    def add_message(self, conversation_id: str, role: str, content: str, source: str = "text"):
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO messages (conversation_id, role, content, source, created_at) "
                "VALUES (?,?,?,?,?)",
                (conversation_id, role, content, source, now)
            )
            self._conn.execute(
                "UPDATE conversations SET updated_at=? WHERE id=?", (now, conversation_id)
            )
            self._conn.commit()

    def get_messages(self, conversation_id: str) -> list:
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content, source, created_at FROM messages "
                "WHERE conversation_id=? ORDER BY id ASC",
                (conversation_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_chat_history(self, conversation_id: str) -> list:
        """Messages in the plain {"role":..., "content":...} shape process_turn expects —
        source/timestamp are for the UI and search, not the LLM."""
        return [{"role": m["role"], "content": m["content"]} for m in self.get_messages(conversation_id)]

    def message_count(self, conversation_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE conversation_id=?", (conversation_id,)
            ).fetchone()
        return row["n"] if row else 0

    # ------------------------------------------------------------------ projects --
    # A project is just a named group of conversations (Claude-Projects-style "folder"),
    # plus an optional pool of shared files every conversation inside it can see. Deleting
    # a project never deletes its conversations — they just lose their project_id and fall
    # back to the plain, ungrouped list. That's a deliberate default: losing a folder
    # label is recoverable friction, losing chat history isn't.

    def create_project(self, name: str) -> str:
        pid = uuid.uuid4().hex
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO projects (id, name, created_at, updated_at) VALUES (?,?,?,?)",
                (pid, (name or "").strip()[:120] or "New project", now, now)
            )
            self._conn.commit()
        return pid

    def project_exists(self, project_id: str) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone()
        return row is not None

    def list_projects(self) -> list:
        with self._lock:
            rows = self._conn.execute(
                "SELECT p.id AS id, p.name AS name, p.created_at AS created_at, p.updated_at AS updated_at, "
                "(SELECT COUNT(*) FROM conversations c WHERE c.project_id = p.id) AS conversation_count "
                "FROM projects p ORDER BY p.updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_project(self, project_id: str):
        with self._lock:
            row = self._conn.execute(
                "SELECT id, name, created_at, updated_at FROM projects WHERE id=?", (project_id,)
            ).fetchone()
        return dict(row) if row else None

    def rename_project(self, project_id: str, name: str):
        name = (name or "").strip()[:120] or "Untitled project"
        with self._lock:
            self._conn.execute(
                "UPDATE projects SET name=?, updated_at=? WHERE id=?", (name, time.time(), project_id)
            )
            self._conn.commit()

    def delete_project(self, project_id: str):
        with self._lock:
            self._conn.execute("UPDATE conversations SET project_id=NULL WHERE project_id=?", (project_id,))
            self._conn.execute("DELETE FROM files WHERE project_id=?", (project_id,))
            self._conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
            self._conn.commit()

    def touch_project(self, project_id: str):
        with self._lock:
            self._conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (time.time(), project_id))
            self._conn.commit()

    # ---------------------------------------------------------------------- files --
    # A file belongs to a conversation (visible only there) or to a project (visible to
    # every conversation inside it) — never both. Uploading while a conversation is
    # attached to a project files it under the project, which is what makes "project
    # knowledge" shared across that project's chats instead of siloed per-chat.

    def add_file(self, filename: str, content_text: str, size_bytes: int,
                 conversation_id: str = None, project_id: str = None,
                 extractable: bool = True, truncated: bool = False) -> str:
        fid = uuid.uuid4().hex
        with self._lock:
            self._conn.execute(
                "INSERT INTO files (id, conversation_id, project_id, filename, content_text, "
                "extractable, truncated, size_bytes, uploaded_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (fid, conversation_id, project_id, filename, content_text,
                 1 if extractable else 0, 1 if truncated else 0, size_bytes, time.time())
            )
            self._conn.commit()
        return fid

    def list_files(self, conversation_id: str = None, project_id: str = None) -> list:
        with self._lock:
            if conversation_id is not None:
                rows = self._conn.execute(
                    "SELECT id, conversation_id, project_id, filename, extractable, truncated, "
                    "size_bytes, uploaded_at FROM files WHERE conversation_id=? ORDER BY uploaded_at DESC",
                    (conversation_id,)
                ).fetchall()
            elif project_id is not None:
                rows = self._conn.execute(
                    "SELECT id, conversation_id, project_id, filename, extractable, truncated, "
                    "size_bytes, uploaded_at FROM files WHERE project_id=? ORDER BY uploaded_at DESC",
                    (project_id,)
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT id, conversation_id, project_id, filename, extractable, truncated, "
                    "size_bytes, uploaded_at FROM files ORDER BY uploaded_at DESC"
                ).fetchall()
        return [dict(r) for r in rows]

    def get_context_files(self, conversation_id: str) -> list:
        """Every file a turn in this conversation should be able to see: files attached
        directly to it, plus — if it belongs to a project — every file attached to that
        project. Returns full rows including content_text (unlike list_files, which is for
        the UI's attachment list and deliberately leaves content_text out to stay light)."""
        conv = self.get_conversation(conversation_id)
        project_id = conv.get("project_id") if conv else None
        with self._lock:
            if project_id:
                rows = self._conn.execute(
                    "SELECT * FROM files WHERE conversation_id=? OR project_id=? ORDER BY uploaded_at ASC",
                    (conversation_id, project_id)
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM files WHERE conversation_id=? ORDER BY uploaded_at ASC",
                    (conversation_id,)
                ).fetchall()
        return [dict(r) for r in rows]

    def get_file(self, file_id: str):
        with self._lock:
            row = self._conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
        return dict(row) if row else None

    def delete_file(self, file_id: str):
        with self._lock:
            self._conn.execute("DELETE FROM files WHERE id=?", (file_id,))
            self._conn.commit()

    # -------------------------------------------------------------------- search --
    def search(self, query: str, limit: int = 30) -> list:
        query = (query or "").strip()
        if not query:
            return []
        fts_query = _sanitize_fts_query(query)
        with self._lock:
            try:
                rows = self._conn.execute(
                    """
                    SELECT c.id AS id, c.title AS title, c.updated_at AS updated_at,
                           snippet(messages_fts, 0, '[', ']', '...', 8) AS snippet
                    FROM messages_fts
                    JOIN messages m ON m.id = messages_fts.rowid
                    JOIN conversations c ON c.id = m.conversation_id
                    WHERE messages_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (fts_query, limit)
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        seen = set()
        results = []
        for r in rows:
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            results.append(dict(r))
        return results

    # ---------------------------------------------------------------- migration --
    def _migrate_legacy_sessions(self):
        """One-time import of data/sessions/*.json (Phase 2d flat-file format) so nothing
        from before this phase gets silently dropped. Each file is renamed with a
        .migrated suffix after import so this never re-runs on the same file."""
        sessions_dir = os.path.join("data", "sessions")
        if not os.path.isdir(sessions_dir):
            return
        for fname in os.listdir(sessions_dir):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(sessions_dir, fname)
            try:
                with open(fpath, 'r') as f:
                    data = json.load(f)
                history = data.get("chat_history", [])
                cid = data.get("session_id") or uuid.uuid4().hex
                if not history or self.conversation_exists(cid):
                    os.rename(fpath, fpath + ".migrated")
                    continue
                now = data.get("updated", time.time())
                first_user = next((m.get("content", "") for m in history if m.get("role") == "user"), "")
                title = (first_user[:60] or "Imported conversation")
                with self._lock:
                    self._conn.execute(
                        "INSERT INTO conversations (id, title, created_at, updated_at, last_device, titled) "
                        "VALUES (?,?,?,?,?,1)",
                        (cid, title, now, now, "")
                    )
                    for m in history:
                        self._conn.execute(
                            "INSERT INTO messages (conversation_id, role, content, source, created_at) "
                            "VALUES (?,?,?,?,?)",
                            (cid, m.get("role", "user"), m.get("content", ""), "text", now)
                        )
                    self._conn.commit()
                os.rename(fpath, fpath + ".migrated")
            except Exception:
                # Never let a corrupt legacy file block startup — it's left as-is (not
                # renamed) so it's still on disk for manual recovery if it ever mattered.
                continue


# Module-level singleton — same pattern as Memory()/AuthorizationCheck() elsewhere in
# this codebase: importing this module is enough to get a working, auto-created store.
store = ConversationStore()

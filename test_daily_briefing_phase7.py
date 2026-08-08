"""
Tests for Phase 7: daily_briefing.py — the proactive morning-briefing background task.

Uses an isolated temp ConversationStore and temp state files throughout (never touches the
real data/jarvis.db or the real .last_briefing_date), same isolation pattern as
test_security_phase3e.py's posture_monitor tests.

Run: python test_daily_briefing_phase7.py
"""

import os
import sys
import json
import shutil
import asyncio
import tempfile
import unittest.mock as mock
from datetime import date

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


import daily_briefing as db
from conversation_store import ConversationStore

# ---------------------------------------------------------------------------
section("1. Config helpers")
# ---------------------------------------------------------------------------
with mock.patch.dict(os.environ, {"JARVIS_BRIEFING_HOUR": "6"}):
    check("_briefing_hour reads the env var", db._briefing_hour() == 6)
with mock.patch.dict(os.environ, {}, clear=True):
    check("_briefing_hour defaults to 8 when unset", db._briefing_hour() == 8)
with mock.patch.dict(os.environ, {"JARVIS_BRIEFING_HOUR": "not-a-number"}):
    check("_briefing_hour falls back to 8 on a garbage value, doesn't raise", db._briefing_hour() == 8)

# ---------------------------------------------------------------------------
section("2. Last-run-date persistence")
# ---------------------------------------------------------------------------
tmp_root = tempfile.mkdtemp(prefix="jarvis_test_briefing_")
orig_last_run_file = db.LAST_RUN_FILE
orig_conv_file = db.BRIEFING_CONV_FILE
orig_store = db.store

db.LAST_RUN_FILE = os.path.join(tmp_root, ".last_briefing_date")
db.BRIEFING_CONV_FILE = os.path.join(tmp_root, ".daily_briefing_conversation_id")

try:
    check("no file yet returns empty string", db._load_last_run_date() == "")
    db._save_last_run_date("2026-08-07")
    check("save/load round-trips", db._load_last_run_date() == "2026-08-07")

    # ---------------------------------------------------------------------------
    section("3. gather_snapshot — per-tool error isolation")
    # ---------------------------------------------------------------------------
    class FakeTool:
        def __init__(self, fn):
            self._fn = fn
        def execute(self, **kwargs):
            return self._fn()

    fake_tools = {
        "check_system_health": FakeTool(lambda: {"status": "ok", "cpu_usage": "3%"}),
        "get_security_posture": FakeTool(lambda: {"windows_defender": "ok"}),
        "read_recent_emails": FakeTool(lambda: (_ for _ in ()).throw(RuntimeError("token.json missing"))),
        "scan_local_logs": FakeTool(lambda: ["No recent errors."]),
    }
    with mock.patch("tools.ALL_TOOLS", fake_tools):
        snapshot = db.gather_snapshot()
    check("all four tools attempted", set(snapshot.keys()) == set(fake_tools.keys()), f"got: {snapshot.keys()}")
    check("a failing tool's error is captured, doesn't abort the whole snapshot",
          "error" in snapshot["read_recent_emails"], f"got: {snapshot['read_recent_emails']}")
    check("other tools' real results still came through despite the one failure",
          snapshot["check_system_health"] == {"status": "ok", "cpu_usage": "3%"}, f"got: {snapshot}")

    # ---------------------------------------------------------------------------
    section("4. compose_briefing")
    # ---------------------------------------------------------------------------
    check("brain=None returns None, not an error string", db.compose_briefing(None, {}) is None)

    class FakeBrainUnavailable:
        def is_available(self):
            return False
    check("an unavailable brain returns None", db.compose_briefing(FakeBrainUnavailable(), {}) is None)

    class FakeProvider:
        def __init__(self, text):
            self._text = text
        def chat_stream(self, messages, system_prompt):
            return iter([self._text])

    class FakeBrainAvailable:
        def __init__(self, text):
            self._brain = FakeProvider(text)
        def is_available(self):
            return True
        def build_prompt(self):
            return "system prompt"

    result = db.compose_briefing(FakeBrainAvailable("Good morning, Devin. Nothing urgent today."), {"a": 1})
    check("a real (fake) provider's output is returned, tags stripped",
          result == "Good morning, Devin. Nothing urgent today.", f"got: {result!r}")

    result_tagged = db.compose_briefing(
        FakeBrainAvailable("All clear. [REMEMBER: something]"), {"a": 1}
    )
    check("REMEMBER tags are left untouched (compose_briefing uses the chat()-style path, not the streaming persist path)",
          "[REMEMBER" in result_tagged, f"got: {result_tagged!r}")

    # ---------------------------------------------------------------------------
    section("5. _maybe_run_briefing — gating logic")
    # ---------------------------------------------------------------------------
    db.store = ConversationStore(path=os.path.join(tmp_root, "test.db"))
    broadcasts = []

    async def fake_broadcast_all(payload):
        broadcasts.append(payload)

    today = date.today().isoformat()

    # Already ran today — should no-op regardless of hour.
    db._save_last_run_date(today)
    with mock.patch("daily_briefing.datetime") as mock_dt:
        mock_dt.now.return_value.hour = 9
        asyncio.run(db._maybe_run_briefing(FakeBrainAvailable("should not appear"), fake_broadcast_all))
    check("already-ran-today short-circuits without composing/broadcasting anything", broadcasts == [])

    # Reset to "not run today", but before the configured hour.
    db._save_last_run_date("2000-01-01")
    with mock.patch("daily_briefing.datetime") as mock_dt, mock.patch.dict(os.environ, {"JARVIS_BRIEFING_HOUR": "8"}):
        mock_dt.now.return_value.hour = 6
        asyncio.run(db._maybe_run_briefing(FakeBrainAvailable("should not appear"), fake_broadcast_all))
    check("before the configured hour, doesn't run yet", broadcasts == [])
    check("and does NOT mark today as done (should still try later today)",
          db._load_last_run_date() == "2000-01-01")

    # Past the hour, not run today yet — should actually run now.
    with mock.patch("daily_briefing.datetime") as mock_dt, mock.patch.dict(os.environ, {"JARVIS_BRIEFING_HOUR": "8"}):
        mock_dt.now.return_value.hour = 9
        asyncio.run(db._maybe_run_briefing(FakeBrainAvailable("Good morning. All quiet."), fake_broadcast_all))

    check("past the hour and not yet run today, it actually runs", len(broadcasts) == 2, f"got: {broadcasts}")
    stream_end = [b for b in broadcasts if b.get("type") == "stream_end"]
    check("posts a real stream_end with the composed text", stream_end and stream_end[0]["full_text"] == "Good morning. All quiet.")
    check("marks today as done afterward", db._load_last_run_date() == today)

    conv_id = stream_end[0]["conversation_id"]
    check("the briefing landed in a conversation titled 'Daily Briefing'",
          db.store.get_conversation(conv_id)["title"] == "Daily Briefing")
    msgs = db.store.get_messages(conv_id)
    check("exactly one message persisted", len(msgs) == 1 and msgs[0]["role"] == "assistant")

    # Running again the same day (later, still past the hour) must NOT double-post.
    broadcasts.clear()
    with mock.patch("daily_briefing.datetime") as mock_dt, mock.patch.dict(os.environ, {"JARVIS_BRIEFING_HOUR": "8"}):
        mock_dt.now.return_value.hour = 14
        asyncio.run(db._maybe_run_briefing(FakeBrainAvailable("should not appear again"), fake_broadcast_all))
    check("a second check later the same day does not post a duplicate briefing", broadcasts == [])
    check("still exactly one message in the conversation", len(db.store.get_messages(conv_id)) == 1)

    # If the brain is unavailable when it's finally time, don't mark today done — retry later.
    db._save_last_run_date("2000-01-01")
    with mock.patch("daily_briefing.datetime") as mock_dt, mock.patch.dict(os.environ, {"JARVIS_BRIEFING_HOUR": "8"}):
        mock_dt.now.return_value.hour = 9
        asyncio.run(db._maybe_run_briefing(None, fake_broadcast_all))
    check("brain unavailable at the right hour: doesn't mark today as done (will retry)",
          db._load_last_run_date() == "2000-01-01")

finally:
    db.LAST_RUN_FILE = orig_last_run_file
    db.BRIEFING_CONV_FILE = orig_conv_file
    db.store = orig_store
    shutil.rmtree(tmp_root, ignore_errors=True)

print(f"\n{'=' * 55}\n{PASS} passed, {FAIL} failed\n{'=' * 55}")
sys.exit(1 if FAIL else 0)

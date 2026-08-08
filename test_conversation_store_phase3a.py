"""
Tests for Phase 3a: SQLite conversation store.

Uses an isolated temp DB path throughout — never touches the real data/jarvis.db —
so this can run repeatedly without polluting real conversation history.

Run: python test_conversation_store_phase3a.py
"""

import os
import sys
import json
import time
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


from conversation_store import ConversationStore

TMP_DIR = tempfile.mkdtemp(prefix="jarvis_test_store_")
DB_PATH = os.path.join(TMP_DIR, "test.db")

# ---------------------------------------------------------------------------
section("1. Create / list / touch ordering")
# ---------------------------------------------------------------------------
store = ConversationStore(path=DB_PATH)

c1 = store.create_conversation(device_id="desktop-cli")
time.sleep(0.01)
c2 = store.create_conversation(device_id="laptop")

check("create_conversation returns distinct ids", c1 != c2)
check("conversation_exists is true for both", store.conversation_exists(c1) and store.conversation_exists(c2))
check("conversation_exists is false for a random id", not store.conversation_exists("not-a-real-id"))

convs = store.list_conversations()
check("list_conversations returns both, newest first", [c["id"] for c in convs[:2]] == [c2, c1],
      f"got order: {[c['id'] for c in convs]}")

store.touch(c1, device_id="desktop-cli")
convs2 = store.list_conversations()
check("touch() moves a conversation back to the front", convs2[0]["id"] == c1, f"got: {convs2[0]}")

# ---------------------------------------------------------------------------
section("2. Messages + chat_history shape")
# ---------------------------------------------------------------------------
store.add_message(c1, "user", "hello jarvis", source="text")
store.add_message(c1, "assistant", "hello, Devin.", source="text")
store.add_message(c1, "user", "what's my cpu usage", source="voice")

msgs = store.get_messages(c1)
check("get_messages returns 3 rows in order", [m["role"] for m in msgs] == ["user", "assistant", "user"])
check("voice source is preserved", msgs[2]["source"] == "voice")
check("message_count matches", store.message_count(c1) == 3)

history = store.get_chat_history(c1)
check("get_chat_history strips down to role/content only",
      history == [
          {"role": "user", "content": "hello jarvis"},
          {"role": "assistant", "content": "hello, Devin."},
          {"role": "user", "content": "what's my cpu usage"},
      ], f"got: {history}")

# ---------------------------------------------------------------------------
section("3. Titling")
# ---------------------------------------------------------------------------
check("new conversation starts untitled", not store.is_titled(c1))
store.rename_conversation(c1, "CPU usage check-in")
check("rename_conversation sets titled=1", store.is_titled(c1))
check("title actually persisted", store.get_conversation(c1)["title"] == "CPU usage check-in")

# ---------------------------------------------------------------------------
section("4. Full-text search")
# ---------------------------------------------------------------------------
store.add_message(c2, "user", "check my bitwarden vault for reused passwords", source="text")
store.add_message(c2, "assistant", "Found 2 reused passwords across 3 items.", source="text")

results = store.search("bitwarden")
check("search finds the conversation containing the term", any(r["id"] == c2 for r in results),
      f"got: {results}")
check("search does not find an unrelated term", not any(r["id"] == c1 for r in store.search("bitwarden")))

results_multiword = store.search("reused passwords")
check("multi-word search still finds it", any(r["id"] == c2 for r in results_multiword))

# adversarial input shouldn't crash the FTS query
try:
    weird = store.search('"unterminated quote AND * OR -')
    check("adversarial search input doesn't raise", True)
except Exception as e:
    check("adversarial search input doesn't raise", False, f"raised: {e}")

check("empty search returns empty list, not an error", store.search("") == [])

# ---------------------------------------------------------------------------
section("5. Delete")
# ---------------------------------------------------------------------------
store.delete_conversation(c2)
check("deleted conversation no longer exists", not store.conversation_exists(c2))
check("deleted conversation's messages are gone too", store.get_messages(c2) == [])
check("deleted conversation drops out of search", not any(r["id"] == c2 for r in store.search("bitwarden")))

# ---------------------------------------------------------------------------
section("6. Legacy data/sessions/*.json migration")
# ---------------------------------------------------------------------------
MIGRATE_ROOT = tempfile.mkdtemp(prefix="jarvis_test_migrate_")
sessions_dir = os.path.join(MIGRATE_ROOT, "data", "sessions")
os.makedirs(sessions_dir, exist_ok=True)

legacy_session_id = "legacy1234"
legacy_payload = {
    "session_id": legacy_session_id,
    "updated": time.time(),
    "chat_history": [
        {"role": "user", "content": "old conversation from phase 2d"},
        {"role": "assistant", "content": "acknowledged."},
    ],
}
with open(os.path.join(sessions_dir, f"{legacy_session_id}.json"), 'w') as f:
    json.dump(legacy_payload, f)

# an empty-history file should just get marked migrated, not error or create a conversation
empty_payload = {"session_id": "empty1", "updated": time.time(), "chat_history": []}
with open(os.path.join(sessions_dir, "empty1.json"), 'w') as f:
    json.dump(empty_payload, f)

cwd = os.getcwd()
try:
    os.chdir(MIGRATE_ROOT)  # _migrate_legacy_sessions looks at "data/sessions" relative to cwd
    migrate_db = os.path.join(MIGRATE_ROOT, "migrate_test.db")
    migrated_store = ConversationStore(path=migrate_db)

    check("legacy session migrated into a real conversation",
          migrated_store.conversation_exists(legacy_session_id))
    mig_history = migrated_store.get_chat_history(legacy_session_id)
    check("migrated chat_history content matches original",
          mig_history == legacy_payload["chat_history"], f"got: {mig_history}")
    check("migrated conversation got an auto-title from the first user message",
          migrated_store.get_conversation(legacy_session_id)["title"] == "old conversation from phase 2d")

    check("legacy file renamed with .migrated suffix",
          os.path.exists(os.path.join(sessions_dir, f"{legacy_session_id}.json.migrated")))
    check("empty-history legacy file also marked migrated, no crash",
          os.path.exists(os.path.join(sessions_dir, "empty1.json.migrated")))
    check("empty-history file did not create a phantom conversation",
          not migrated_store.conversation_exists("empty1"))

    # re-running migration on an already-migrated dir should be a no-op (files already renamed)
    migrated_store2 = ConversationStore(path=os.path.join(MIGRATE_ROOT, "migrate_test2.db"))
    check("second store on same legacy dir finds nothing left to migrate (already renamed)",
          not migrated_store2.conversation_exists(legacy_session_id))
finally:
    os.chdir(cwd)

# ---------------------------------------------------------------------------
shutil.rmtree(TMP_DIR, ignore_errors=True)
shutil.rmtree(MIGRATE_ROOT, ignore_errors=True)

print(f"\n{'=' * 55}\n{PASS} passed, {FAIL} failed\n{'=' * 55}")
sys.exit(1 if FAIL else 0)

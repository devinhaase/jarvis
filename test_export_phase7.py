"""
Tests for Phase 7: GET /export/{conversation_id} — conversation export as markdown.

Run: python test_export_phase7.py
"""

import os
import sys
import time
import threading
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


TEST_PORT = 8769
os.environ["JARVIS_SERVER_PORT"] = str(TEST_PORT)

import server as server_module
import uvicorn
import requests
from conversation_store import ConversationStore

tmp_db = os.path.join(tempfile.gettempdir(), "jarvis_test_export.db")
if os.path.exists(tmp_db):
    os.remove(tmp_db)
server_module.store = ConversationStore(path=tmp_db)
store = server_module.store

conv_id = store.create_conversation(device_id="test")
store.rename_conversation(conv_id, "Weird / Title: With Punctuation!")
store.add_message(conv_id, "user", "What's my CPU usage?", source="text")
store.add_message(conv_id, "assistant", "Your CPU usage is 5%.", source="text")
store.add_message(conv_id, "user", "thanks", source="voice")

config = uvicorn.Config(server_module.app, host="127.0.0.1", port=TEST_PORT, log_level="warning")
uv_server = uvicorn.Server(config)
threading.Thread(target=uv_server.run, daemon=True).start()

up = False
for _ in range(50):
    try:
        requests.get(f"http://127.0.0.1:{TEST_PORT}/health", timeout=1)
        up = True
        break
    except Exception:
        time.sleep(0.2)
if not up:
    print("[FAIL] server never came up")
    sys.exit(1)

BASE_URL = f"http://127.0.0.1:{TEST_PORT}"

# ---------------------------------------------------------------------------
section("1. Export a real conversation")
# ---------------------------------------------------------------------------
resp = requests.get(f"{BASE_URL}/export/{conv_id}")
check("returns 200", resp.status_code == 200, f"got: {resp.status_code}")
check("content-type is markdown", "markdown" in resp.headers.get("content-type", ""), f"got: {resp.headers}")
check("has a Content-Disposition attachment header (triggers a real download, not inline)",
      "attachment" in resp.headers.get("content-disposition", ""), f"got: {resp.headers}")

body = resp.text
check("includes the conversation title as a heading", "# Weird / Title: With Punctuation!" in body, f"got: {body[:200]}")
check("includes both speakers' content", "What's my CPU usage?" in body and "Your CPU usage is 5%." in body)
check("marks the voice-sourced message as such", "_(voice)_" in body, f"got: {body}")
check("includes an export timestamp note", "_Exported" in body)

# ---------------------------------------------------------------------------
section("2. Filename handles punctuation in the title safely")
# ---------------------------------------------------------------------------
disposition = resp.headers.get("content-disposition", "")
check("filename doesn't contain raw slashes/colons/punctuation from the title (safe for a filesystem)",
      "/" not in disposition.split("filename=")[-1] and ":" not in disposition.split("filename=")[-1],
      f"got: {disposition}")

# ---------------------------------------------------------------------------
section("3. Nonexistent conversation")
# ---------------------------------------------------------------------------
resp404 = requests.get(f"{BASE_URL}/export/not-a-real-conversation-id")
check("returns 404 for a conversation that doesn't exist", resp404.status_code == 404, f"got: {resp404.status_code}")

uv_server.should_exit = True
time.sleep(0.5)
try:
    os.remove(tmp_db)
except OSError:
    pass

print(f"\n{'=' * 55}\n{PASS} passed, {FAIL} failed\n{'=' * 55}")
sys.exit(1 if FAIL else 0)

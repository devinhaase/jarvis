"""
Tests for Phase 4b: server.py's projects, file uploads, tools catalog, and browser
transcription endpoints.

Runs the real FastAPI/uvicorn server in a background thread (isolated device registry +
isolated conversation store, never the real ones), drives REST endpoints with `requests`
and the websocket protocol with `websockets`, exactly like test_server_phase3.py.

Run: python test_server_projects_files_phase4.py
"""

import os
import sys
import io
import json
import time
import struct
import asyncio
import threading
import tempfile

TEST_PORT = 8768
os.environ["JARVIS_SERVER_PORT"] = str(TEST_PORT)

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


import server as server_module
import uvicorn
import websockets
import requests
from device_registry import DeviceRegistry
from conversation_store import ConversationStore

_test_devices_file = os.path.join(tempfile.gettempdir(), "jarvis_test_devices_p4.json")
if os.path.exists(_test_devices_file):
    os.remove(_test_devices_file)
server_module.registry = DeviceRegistry(path=_test_devices_file)

_test_db = os.path.join(tempfile.gettempdir(), "jarvis_test_store_p4.db")
if os.path.exists(_test_db):
    os.remove(_test_db)
server_module.store = ConversationStore(path=_test_db)

DESKTOP_ID = "desktop-cli"
DESKTOP_TOKEN = server_module.registry.register(DESKTOP_ID, "Test Desktop", ["filesystem", "microphone"])


class FakeProvider:
    def __init__(self, rounds):
        self._rounds = list(rounds)
        self.calls = 0
        self.system_prompts = []

    def chat_stream(self, messages, system_prompt):
        self.calls += 1
        self.system_prompts.append(system_prompt)
        if not self._rounds:
            return iter(["(no more canned rounds)"])
        return iter(self._rounds.pop(0))


def set_fake_rounds(rounds):
    provider = FakeProvider(rounds)
    server_module.brain._brain = provider
    server_module.brain._llm_error = None
    return provider


# Background loops fire an immediate check on startup and broadcast to every connected
# socket — no-op them so they can't collide with this test's own expected events (same fix
# test_teams_phase4.py's suite applied after finding this for real).
async def _noop_background_loop(*args, **kwargs):
    await asyncio.Event().wait()

import posture_monitor, daily_briefing, team_board_dispatcher as _tbd, deep_reflection as _dr
posture_monitor.posture_monitor_loop = _noop_background_loop
daily_briefing.daily_briefing_loop = _noop_background_loop
_tbd.team_board_dispatch_loop = _noop_background_loop
_dr.deep_reflection_loop = _noop_background_loop

config = uvicorn.Config(server_module.app, host="127.0.0.1", port=TEST_PORT, log_level="warning")
uv_server = uvicorn.Server(config)
server_thread = threading.Thread(target=uv_server.run, daemon=True)
server_thread.start()

import urllib.request
up = False
for _ in range(50):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{TEST_PORT}/health", timeout=1)
        up = True
        break
    except Exception:
        time.sleep(0.2)
if not up:
    print("[FAIL] server never came up")
    sys.exit(1)

BASE_URL = f"http://127.0.0.1:{TEST_PORT}"
WS_URL = f"ws://127.0.0.1:{TEST_PORT}/ws"


async def hello(ws, conversation_id=None):
    await ws.send(json.dumps({
        "type": "hello", "device_id": DESKTOP_ID, "token": DESKTOP_TOKEN,
        "capabilities": ["filesystem", "microphone"], "conversation_id": conversation_id,
    }))
    return json.loads(await ws.recv())


async def recv_until(ws, expected_type, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = json.loads(await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.time())))
        if data.get("type") == expected_type:
            return data
    raise AssertionError(f"never received a {expected_type!r} message within {timeout}s")


async def send_message_and_collect(ws, text):
    await ws.send(json.dumps({"type": "message", "text": text, "source": "text"}))
    events = []
    while True:
        data = json.loads(await ws.recv())
        events.append(data)
        if data.get("type") == "stream_end":
            break
    return events


async def main():
    section("1. Tools catalog")
    async with websockets.connect(WS_URL) as ws:
        await hello(ws)
        await ws.send(json.dumps({"type": "list_tools"}))
        listing = await recv_until(ws, "tool_list")
        tools = listing["tools"]
        check("tool_list returns a non-empty catalog", len(tools) > 0)
        names = {t["name"] for t in tools}
        check("catalog includes a known security tool", "get_security_posture" in names, f"got sample: {list(names)[:5]}")
        sample = next(t for t in tools if t["name"] == "get_security_posture")
        check("each tool entry has name/description/tier/role",
              all(k in sample for k in ("name", "description", "tier", "role")), f"got: {sample}")

    section("2. Project CRUD over the protocol")
    async with websockets.connect(WS_URL) as ws:
        await hello(ws)
        await ws.send(json.dumps({"type": "create_project", "name": "Website Redesign"}))
        created = await recv_until(ws, "project_created")
        pid = created["project_id"]
        check("create_project returns a project_id", bool(pid))

        await ws.send(json.dumps({"type": "list_projects"}))
        listing = await recv_until(ws, "project_list")
        check("new project shows up in list_projects", any(p["id"] == pid for p in listing["projects"]),
              f"got: {listing}")

        await ws.send(json.dumps({"type": "rename_project", "project_id": pid, "name": "Website Redesign 2.0"}))
        renamed = await recv_until(ws, "project_list")
        check("rename_project broadcasts an updated project_list",
              any(p["id"] == pid and p["name"] == "Website Redesign 2.0" for p in renamed["projects"]),
              f"got: {renamed}")

    section("3. Assigning a conversation to a project, filtered listing")
    async with websockets.connect(WS_URL) as ws:
        ready = await hello(ws)
        conv_id = ready["conversation_id"]
        check("fresh conversation has no project_id in the ready handshake", ready.get("project_id") is None)

        await ws.send(json.dumps({"type": "set_conversation_project", "conversation_id": conv_id, "project_id": pid}))
        await recv_until(ws, "conversation_list_changed")

        await ws.send(json.dumps({"type": "list_conversations", "project_id": pid}))
        filtered = await recv_until(ws, "conversation_list")
        check("filtered list_conversations shows the reassigned conversation",
              any(c["id"] == conv_id for c in filtered["conversations"]), f"got: {filtered}")

        await ws.send(json.dumps({"type": "new_conversation", "project_id": pid}))
        opened = await recv_until(ws, "conversation_opened")
        check("new_conversation with project_id creates it inside that project",
              opened.get("project_id") == pid, f"got: {opened}")

    section("4. File upload via REST, visible over the protocol, injected into the system prompt")
    async with websockets.connect(WS_URL) as ws:
        ready = await hello(ws)
        upload_conv = ready["conversation_id"]

    resp = requests.post(
        f"{BASE_URL}/upload",
        data={"conversation_id": upload_conv},
        files={"file": ("notes.txt", io.BytesIO(b"The launch code is PINEAPPLE-42."), "text/plain")},
    )
    check("upload endpoint returns 200", resp.status_code == 200, f"got: {resp.status_code} {resp.text}")
    meta = resp.json()
    check("upload response reports it as extractable text", meta.get("extractable") is True, f"got: {meta}")
    file_id = meta["id"]

    async with websockets.connect(WS_URL) as ws:
        await hello(ws, conversation_id=upload_conv)
        await ws.send(json.dumps({"type": "list_files", "conversation_id": upload_conv}))
        listing = await recv_until(ws, "file_list")
        check("uploaded file appears in list_files", any(f["id"] == file_id for f in listing["files"]),
              f"got: {listing}")

        provider = set_fake_rounds([["Noted."]])
        await send_message_and_collect(ws, "what's in my notes?")
        check("the uploaded file's content was folded into the system prompt sent to the LLM",
              any("PINEAPPLE-42" in p for p in provider.system_prompts),
              f"system prompts did not contain the file content")

    section("5. Uploading too-large a file is rejected")
    import file_ingest
    too_big = b"x" * (file_ingest.MAX_UPLOAD_BYTES + 1)
    resp = requests.post(
        f"{BASE_URL}/upload",
        data={"conversation_id": upload_conv},
        files={"file": ("huge.txt", io.BytesIO(too_big), "text/plain")},
    )
    check("oversized upload is rejected with 413", resp.status_code == 413, f"got: {resp.status_code}")

    section("6. Delete file")
    async with websockets.connect(WS_URL) as ws:
        await hello(ws, conversation_id=upload_conv)
        await ws.send(json.dumps({"type": "delete_file", "file_id": file_id}))
        deleted = await recv_until(ws, "file_deleted")
        check("delete_file confirms deletion", deleted.get("file_id") == file_id)
        check("file actually gone from the store", server_module.store.get_file(file_id) is None)

    section("7. /transcribe endpoint — plumbing works, doesn't crash on non-speech audio")
    sample_rate = 16000
    duration = 1.0
    freq = 440
    n_samples = int(sample_rate * duration)
    samples = [int(3000 * __import__("math").sin(2 * __import__("math").pi * freq * i / sample_rate)) for i in range(n_samples)]
    tone_pcm = struct.pack('<' + 'h' * len(samples), *samples)

    resp = requests.post(
        f"{BASE_URL}/transcribe",
        files={"audio": ("clip.raw", io.BytesIO(tone_pcm), "application/octet-stream")},
    )
    check("transcribe endpoint returns 200 for real (non-speech) audio", resp.status_code == 200, f"got: {resp.status_code} {resp.text}")
    check("transcribe response has a 'text' key", "text" in resp.json(), f"got: {resp.json()}")

    resp_empty = requests.post(
        f"{BASE_URL}/transcribe",
        files={"audio": ("empty.raw", io.BytesIO(b""), "application/octet-stream")},
    )
    check("empty audio doesn't crash the endpoint", resp_empty.status_code == 200 and resp_empty.json().get("text") == "",
          f"got: {resp_empty.status_code} {resp_empty.text}")


asyncio.run(main())

uv_server.should_exit = True
time.sleep(0.5)

for f in (_test_devices_file, _test_db):
    try:
        os.remove(f)
    except OSError:
        pass

print(f"\n{'=' * 55}\n{PASS} passed, {FAIL} failed\n{'=' * 55}")
sys.exit(1 if FAIL else 0)

"""
Tests for Phase 3b/3c/3d: the rewritten multi-device server — conversation-centric
protocol (not per-session flat files), streaming turns, multi-client broadcast to every
websocket watching the same conversation, and the carried-over Phase 2d guarantees (auth,
capability intersection, Tier-3/4 approval bridge) under the renamed protocol fields.

Runs the real FastAPI/uvicorn server in a background thread inside this process, against an
isolated device registry AND an isolated conversation store (never the real devices.json or
data/jarvis.db), with the shared JarvisBrain's LLM swapped for a scripted FakeProvider so
tool-triggering behavior is deterministic rather than depending on what a real model decides
to do. One live test at the end round-trips through the real local Ollama model too.

Run: python test_server_phase3.py
"""

import os
import sys
import json
import time
import asyncio
import threading
import tempfile

TEST_PORT = 8767
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
from device_registry import DeviceRegistry
from conversation_store import ConversationStore

# Isolate this test's device registrations and conversation history from the real files.
_test_devices_file = os.path.join(tempfile.gettempdir(), "jarvis_test_devices_p3.json")
if os.path.exists(_test_devices_file):
    os.remove(_test_devices_file)
server_module.registry = DeviceRegistry(path=_test_devices_file)

_test_db = os.path.join(tempfile.gettempdir(), "jarvis_test_store_p3.db")
if os.path.exists(_test_db):
    os.remove(_test_db)
server_module.store = ConversationStore(path=_test_db)

server_module.APPROVAL_TIMEOUT = {"TIER_3": 1.0, "TIER_4": 1.2}
server_module.APPROVAL_DEFAULT = {"TIER_3": True, "TIER_4": False}

DESKTOP_ID = "desktop-cli"
DESKTOP_TOKEN = server_module.registry.register(DESKTOP_ID, "Test Desktop", ["filesystem", "microphone"])

RESTRICTED_ID = "test-mobile"
RESTRICTED_TOKEN = server_module.registry.register(RESTRICTED_ID, "Test Mobile", ["filesystem"])  # no microphone

NO_FS_ID = "test-web-gui"
NO_FS_TOKEN = server_module.registry.register(NO_FS_ID, "Test Browser", [])  # no capabilities at all


class FakeProvider:
    """Swaps in for JarvisBrain._brain so tool-triggering behavior is deterministic."""
    def __init__(self, rounds):
        self._rounds = list(rounds)
        self.calls = 0

    def chat_stream(self, messages, system_prompt):
        self.calls += 1
        if not self._rounds:
            return iter(["(no more canned rounds)"])
        return iter(self._rounds.pop(0))


def set_fake_rounds(rounds):
    server_module.brain._brain = FakeProvider(rounds)
    server_module.brain._llm_error = None


# Background loops (posture_monitor/daily_briefing/team_board/deep_reflection) fire an
# immediate check on startup and broadcast to every connected socket — no-op them so they
# can't collide with this test's own expected events (same fix test_teams_phase4.py's
# suite applied after finding this for real).
async def _noop_background_loop(*args, **kwargs):
    await asyncio.Event().wait()

import posture_monitor, daily_briefing, team_board_dispatcher as _tbd, deep_reflection as _dr
posture_monitor.posture_monitor_loop = _noop_background_loop
daily_briefing.daily_briefing_loop = _noop_background_loop
_tbd.team_board_dispatch_loop = _noop_background_loop
_dr.deep_reflection_loop = _noop_background_loop

# --- Start the real server in a background thread ---
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

WS_URL = f"ws://127.0.0.1:{TEST_PORT}/ws"


async def hello(ws, device_id, token, caps, conversation_id=None):
    await ws.send(json.dumps({
        "type": "hello", "device_id": device_id, "token": token,
        "capabilities": caps, "conversation_id": conversation_id,
    }))
    return json.loads(await ws.recv())


async def recv_until(ws, expected_type, timeout=5):
    """Drain messages until one of the expected type arrives — needed anywhere a background
    broadcast (auto-titling's conversation_renamed, another client's activity) could land on
    the socket between a request and its specific reply."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = json.loads(await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.time())))
        if data.get("type") == expected_type:
            return data
    raise AssertionError(f"never received a {expected_type!r} message within {timeout}s")


async def send_message_and_collect(ws, text, source="text"):
    """Send a message and drain events until stream_end, returning (chunks, events)."""
    await ws.send(json.dumps({"type": "message", "text": text, "source": source}))
    chunks = []
    events = []
    while True:
        data = json.loads(await ws.recv())
        events.append(data)
        if data.get("type") == "stream_chunk":
            chunks.append(data.get("text", ""))
        if data.get("type") == "stream_end":
            break
    return chunks, events


async def main():
    section("1. Auth — wrong token rejected, valid token accepted with a fresh conversation")
    async with websockets.connect(WS_URL) as ws:
        msg = await hello(ws, DESKTOP_ID, "wrong-token", ["filesystem"])
        check("wrong token gets an error response", msg.get("type") == "error", f"got: {msg}")
        try:
            await ws.recv()
            check("connection closes after rejecting bad auth", False, "still open")
        except websockets.exceptions.ConnectionClosed as e:
            check("connection closes after rejecting bad auth", e.code == 4001, f"code={e.code}")

    async with websockets.connect(WS_URL) as ws:
        ready = await hello(ws, DESKTOP_ID, DESKTOP_TOKEN, ["filesystem", "microphone"])
        check("valid token gets type=ready", ready.get("type") == "ready", f"got: {ready}")
        check("ready includes a conversation_id (auto-created)", bool(ready.get("conversation_id")))
        check("ready reflects requested capabilities (fully granted)",
              set(ready.get("capabilities", [])) == {"filesystem", "microphone"})
        check("brand new conversation has 0 resumed turns", ready.get("resumed_turns") == 0)

    section("2. Capability intersection — device can't self-escalate beyond registration")
    async with websockets.connect(WS_URL) as ws:
        ready = await hello(ws, RESTRICTED_ID, RESTRICTED_TOKEN, ["filesystem", "microphone"])
        check("server grants only the intersection with allowed_capabilities",
              ready.get("capabilities") == ["filesystem"], f"got: {ready.get('capabilities')}")

    section("3. Streaming a plain turn (deterministic via FakeProvider)")
    set_fake_rounds([["Hello ", "Devin, ", "all set."]])
    conv_id = None
    async with websockets.connect(WS_URL) as ws:
        ready = await hello(ws, DESKTOP_ID, DESKTOP_TOKEN, ["filesystem", "microphone"])
        conv_id = ready["conversation_id"]
        chunks, events = await send_message_and_collect(ws, "hi there")
        check("received multiple stream_chunk events", len(chunks) >= 1, f"got: {chunks}")
        check("joined chunks match the canned response", "".join(chunks) == "Hello Devin, all set.",
              f"got: {''.join(chunks)!r}")
        stream_end = [e for e in events if e.get("type") == "stream_end"][0]
        check("stream_end carries the full text too", stream_end.get("full_text") == "Hello Devin, all set.",
              f"got: {stream_end}")
        # The sender does NOT get its own message echoed back (it already rendered it
        # optimistically) — only OTHER watchers of the same conversation get user_message.
        # See section 6 below for that broadcast-to-others behavior.
        user_msg_events = [e for e in events if e.get("type") == "user_message"]
        check("the sender itself does not receive its own user_message echoed back",
              user_msg_events == [], f"got: {user_msg_events}")

    from conversation_store import store as _unused  # noqa — ensure module import path sane
    persisted = server_module.store.get_messages(conv_id)
    check("both turns persisted to the conversation store",
          [m["role"] for m in persisted] == ["user", "assistant"], f"got: {persisted}")
    check("assistant message content matches what streamed",
          persisted[1]["content"] == "Hello Devin, all set.")

    section("4. Reconnect with the same conversation_id resumes history")
    async with websockets.connect(WS_URL) as ws:
        ready = await hello(ws, DESKTOP_ID, DESKTOP_TOKEN, ["filesystem"], conversation_id=conv_id)
        check("reconnect with explicit conversation_id gets the same conversation back",
              ready.get("conversation_id") == conv_id)
        check("resumed_turns reflects prior messages", ready.get("resumed_turns") == 2,
              f"got: {ready.get('resumed_turns')}")

    section("5. Conversation management: new / list / rename / search / delete")
    async with websockets.connect(WS_URL) as ws:
        ready = await hello(ws, DESKTOP_ID, DESKTOP_TOKEN, ["filesystem"], conversation_id=conv_id)

        await ws.send(json.dumps({"type": "new_conversation"}))
        opened = await recv_until(ws, "conversation_opened")
        check("new_conversation returns a fresh, different conversation_id",
              opened.get("conversation_id") != conv_id, f"got: {opened}")
        second_conv = opened["conversation_id"]

        await ws.send(json.dumps({"type": "list_conversations"}))
        listing = await recv_until(ws, "conversation_list")
        ids = [c["id"] for c in listing.get("conversations", [])]
        check("list_conversations includes both conversations", conv_id in ids and second_conv in ids,
              f"got: {ids}")

        await ws.send(json.dumps({"type": "rename_conversation", "conversation_id": conv_id, "title": "Renamed by test"}))
        renamed = await recv_until(ws, "conversation_renamed")
        check("rename_conversation broadcasts conversation_renamed",
              renamed.get("conversation_id") == conv_id and renamed.get("title") == "Renamed by test",
              f"got: {renamed}")

        set_fake_rounds([["searchable marker phrase xyz123"]])
        await ws.send(json.dumps({"type": "open_conversation", "conversation_id": second_conv}))
        await recv_until(ws, "conversation_opened")
        _, _ = await send_message_and_collect(ws, "trigger the marker phrase")

        # second_conv is still untitled, so an auto-title conversation_renamed broadcast may
        # land here too before search_results — recv_until skips past it either way.
        await ws.send(json.dumps({"type": "search_conversations", "query": "marker phrase"}))
        results = await recv_until(ws, "search_results")
        check("search_conversations finds the conversation containing the streamed text",
              any(r["id"] == second_conv for r in results.get("results", [])),
              f"got: {results}")

        await ws.send(json.dumps({"type": "delete_conversation", "conversation_id": second_conv}))
        deleted = await recv_until(ws, "conversation_deleted")
        check("delete_conversation confirms deletion", deleted.get("conversation_id") == second_conv)
        check("deleted conversation is actually gone from the store",
              not server_module.store.conversation_exists(second_conv))

    section("6. Multi-client broadcast — a second socket watching the same conversation sees live turns")
    set_fake_rounds([["Broadcast ", "to ", "everyone watching."]])
    async with websockets.connect(WS_URL) as ws_a, websockets.connect(WS_URL) as ws_b:
        ready_a = await hello(ws_a, DESKTOP_ID, DESKTOP_TOKEN, ["filesystem"], conversation_id=conv_id)
        ready_b = await hello(ws_b, RESTRICTED_ID, RESTRICTED_TOKEN, ["filesystem"], conversation_id=conv_id)
        check("both sockets attached to the same conversation", ready_a["conversation_id"] == ready_b["conversation_id"] == conv_id)

        await ws_a.send(json.dumps({"type": "message", "text": "does the other tab see this?"}))

        # Drain ws_b independently — it never sent anything, but should see the whole turn live.
        b_events = []
        while True:
            data = json.loads(await asyncio.wait_for(ws_b.recv(), timeout=5))
            b_events.append(data)
            if data.get("type") == "stream_end":
                break
        check("the non-sending socket received the user_message event",
              any(e.get("type") == "user_message" and e.get("text") == "does the other tab see this?" for e in b_events),
              f"got types: {[e.get('type') for e in b_events]}")
        check("the non-sending socket received streamed chunks too",
              any(e.get("type") == "stream_chunk" for e in b_events))
        check("the non-sending socket received stream_end with the full text",
              any(e.get("type") == "stream_end" and e.get("full_text") == "Broadcast to everyone watching." for e in b_events))

        # drain ws_a's own copy of the same turn so it doesn't leak into a later recv()
        while True:
            data = json.loads(await asyncio.wait_for(ws_a.recv(), timeout=5))
            if data.get("type") == "stream_end":
                break

        section("6b. voice_status broadcasts to other watchers of the same conversation")
        await ws_a.send(json.dumps({"type": "voice_status", "state": "listening"}))
        vs = json.loads(await asyncio.wait_for(ws_b.recv(), timeout=5))
        check("voice_status from one client reaches the other watcher",
              vs.get("type") == "voice_status" and vs.get("state") == "listening" and vs.get("device_id") == DESKTOP_ID,
              f"got: {vs}")

    section("7. Capability filtering end-to-end — a tool call gets refused, not executed")
    set_fake_rounds([
        ['[TOOL: check_system_health {}]'],
        ["Could not check — no filesystem capability."],
    ])
    async with websockets.connect(WS_URL) as ws:
        await hello(ws, NO_FS_ID, NO_FS_TOKEN, [])
        _, events = await send_message_and_collect(ws, "how's my cpu")
        stream_end = [e for e in events if e.get("type") == "stream_end"][0]
        check("denied list reports the blocked tool",
              stream_end.get("denied") == [{"tool": "check_system_health", "required": "filesystem"}],
              f"got: {stream_end}")
        check("tools_ran is empty since the only requested tool was blocked",
              stream_end.get("tools_ran") == [])

    section("8. Auto-titling — untitled conversation gets renamed after its first exchange")
    set_fake_rounds([["This is the assistant's first reply in a brand new conversation."]])
    async with websockets.connect(WS_URL) as ws:
        ready = await hello(ws, DESKTOP_ID, DESKTOP_TOKEN, ["filesystem"])
        fresh_conv = ready["conversation_id"]
        check("fresh conversation starts untitled", not server_module.store.is_titled(fresh_conv))

        await send_message_and_collect(ws, "what's the weather like on the moon")

        # Auto-titling is fire-and-forget (asyncio.create_task) — give it a moment, and it
        # uses the same FakeProvider queue, which we've now exhausted (empty), so
        # _generate_title() will get the "no more canned rounds" filler and fall back.
        renamed = None
        for _ in range(20):
            try:
                renamed = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.3))
                if renamed.get("type") == "conversation_renamed" and renamed.get("conversation_id") == fresh_conv:
                    break
            except asyncio.TimeoutError:
                pass
        check("conversation eventually gets auto-renamed (broadcast conversation_renamed)",
              renamed is not None and renamed.get("type") == "conversation_renamed", f"got: {renamed}")
        check("conversation is marked titled afterward", server_module.store.is_titled(fresh_conv))

    section("9. Approval bridge — worker thread -> event loop -> connected client -> back")
    async with websockets.connect(WS_URL) as ws:
        await hello(ws, DESKTOP_ID, DESKTOP_TOKEN, ["filesystem"])

        from tools import Tier
        approval_fn = server_module.make_approval_fn()
        result_holder = {}

        def call_from_worker_thread():
            result_holder["result"] = approval_fn("test_tier4_action", Tier.TIER_4)

        t = threading.Thread(target=call_from_worker_thread)
        t.start()

        approval_request = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        check("worker-thread approval request reaches the connected client",
              approval_request.get("type") == "approval_request" and approval_request.get("tier") == "TIER_4",
              f"got: {approval_request}")

        await ws.send(json.dumps({
            "type": "approve", "approval_id": approval_request.get("approval_id"), "approved": True,
        }))
        t.join(timeout=5)
        check("client's approval resolves the worker thread's blocking call",
              result_holder.get("result") is True, f"got: {result_holder}")

    section("10. Approval timeout defaults — TIER_3 proceeds, TIER_4 denies, when nobody answers")
    for _ in range(20):
        if len(server_module.connected) == 0:
            break
        await asyncio.sleep(0.1)
    check("no devices connected for this check", len(server_module.connected) == 0,
          f"connected={len(server_module.connected)}")

    from tools import Tier
    t3_result = await asyncio.to_thread(lambda: server_module.make_approval_fn()("unanswered_tier3", Tier.TIER_3))
    check("TIER_3 with no response defaults to proceed (True)", t3_result is True, f"got: {t3_result}")
    t4_result = await asyncio.to_thread(lambda: server_module.make_approval_fn()("unanswered_tier4", Tier.TIER_4))
    check("TIER_4 with no response defaults to deny (False)", t4_result is False, f"got: {t4_result}")

    section("11. Live smoke test — real local Ollama round trip through the streaming protocol")
    try:
        import requests as _r
        _r.get(os.getenv("OLLAMA_URL", "http://localhost:11434"), timeout=2)
        ollama_up = True
    except Exception:
        ollama_up = False

    if not ollama_up:
        print("  [SKIP] Ollama not reachable — skipping live end-to-end check.")
    else:
        from llm import get_llm
        server_module.brain._brain = get_llm()
        server_module.brain._llm_error = None
        async with websockets.connect(WS_URL) as ws:
            await hello(ws, DESKTOP_ID, DESKTOP_TOKEN, ["filesystem"])
            chunks, events = await send_message_and_collect(ws, "Reply with exactly the word: online")
            check("live model round trip produced at least one chunk", len(chunks) >= 1, f"got: {chunks}")
            check("live model round trip produced non-empty final text",
                  len("".join(chunks).strip()) > 0, f"got: {''.join(chunks)!r}")


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

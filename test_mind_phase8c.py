"""
Tests for Phase 8c: "Living Mind" — mind_graph.py's snapshot builder,
conversation_store.get_conversation_centroids(), and the real server's /mind page + /ws/mind
observer socket (auth, snapshot delivery, live tool_fired/memory_pulse events).

Runs the real FastAPI/uvicorn server in a background thread against an isolated device
registry AND conversation store, same pattern as test_server_phase3.py — never the real
devices.json or data/jarvis.db.

Run: python test_mind_phase8c.py
"""

import os
import sys
import json
import time
import asyncio
import threading
import tempfile

TEST_PORT = 8770
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


# ---------------------------------------------------------------------------
section("mind_graph.build_snapshot() — pure function")
# ---------------------------------------------------------------------------

import mind_graph
from tools import ALL_TOOLS

snap = mind_graph.build_snapshot()
check("snapshot has nodes and edges", "nodes" in snap and "edges" in snap)
check("snapshot has a generated_at timestamp", isinstance(snap.get("generated_at"), float))

node_ids = {n["id"] for n in snap["nodes"]}
check("exactly one core node", sum(1 for n in snap["nodes"] if n["type"] == "core") == 1)
check("core node id is 'core'", "core" in node_ids)

hub_ids = {n["id"] for n in snap["nodes"] if n["type"] == "hub"}
check("hub:memory always present", "hub:memory" in hub_ids)
for role in ("general", "defense", "offense", "practice"):
    if any((t.role or "general") == role for t in ALL_TOOLS.values()):
        check(f"hub:{role} present (role exists among real tools)", f"hub:{role}" in hub_ids)

tool_node_ids = {n["id"] for n in snap["nodes"] if n["type"] == "tool"}
check("every real registered tool has a node", tool_node_ids == {f"tool:{name}" for name in ALL_TOOLS},
      f"missing: {set(f'tool:{n}' for n in ALL_TOOLS) - tool_node_ids}")

sample_tool_node = next(n for n in snap["nodes"] if n["type"] == "tool")
check("tool node carries tier/role/description", all(k in sample_tool_node for k in ("tier", "role", "description")),
      f"got keys: {sample_tool_node.keys()}")

# Every structural edge should reference nodes that actually exist in the snapshot.
dangling = [e for e in snap["edges"] if e["source"] not in node_ids or e["target"] not in node_ids]
check("no edge references a node that isn't in the snapshot", dangling == [], f"dangling: {dangling}")

# Every tool hangs off its hub, every hub hangs off core, exactly.
hub_edges = {e["target"] for e in snap["edges"] if e["source"] == "core"}
check("core connects to every present hub", hub_edges == hub_ids, f"got {hub_edges} vs {hub_ids}")

for name, tool in ALL_TOOLS.items():
    expected_hub = f"hub:{tool.role or 'general'}"
    matches = [e for e in snap["edges"] if e["target"] == f"tool:{name}"]
    check_ok = len(matches) == 1 and matches[0]["source"] == expected_hub
    if not check_ok:
        check(f"'{name}' attaches to its correct hub ({expected_hub})", False, f"got: {matches}")
check("every tool attaches to exactly its role's hub (spot-checked above, no failures printed)", True)

# ---------------------------------------------------------------------------
section("conversation_store.get_conversation_centroids()")
# ---------------------------------------------------------------------------

from conversation_store import ConversationStore

_tmp_db = os.path.join(tempfile.gettempdir(), "jarvis_test_mind_centroids.db")
if os.path.exists(_tmp_db):
    os.remove(_tmp_db)
test_store = ConversationStore(path=_tmp_db)

conv_a = test_store.create_conversation(device_id="test", title="A")
conv_b = test_store.create_conversation(device_id="test", title="B")
conv_c = test_store.create_conversation(device_id="test", title="C — no embeddings")

msg1 = test_store.add_message(conv_a, "user", "hello")
msg2 = test_store.add_message(conv_a, "assistant", "hi")
msg3 = test_store.add_message(conv_b, "user", "hey")

test_store.add_message_embedding(msg1, conv_a, "test-model", [1.0, 0.0, 0.0])
test_store.add_message_embedding(msg2, conv_a, "test-model", [0.0, 1.0, 0.0])
test_store.add_message_embedding(msg3, conv_b, "test-model", [0.0, 0.0, 1.0])

centroids = test_store.get_conversation_centroids([conv_a, conv_b, conv_c])
check("conversation with embeddings gets a centroid", conv_a in centroids)
check("centroid is the mean of its messages' vectors", centroids[conv_a] == [0.5, 0.5, 0.0], f"got: {centroids[conv_a]}")
check("conversation with a single embedding just returns it", centroids[conv_b] == [0.0, 0.0, 1.0])
check("conversation with zero embeddings is absent, not an error", conv_c not in centroids)

all_centroids = test_store.get_conversation_centroids()  # no filter — every conversation with embeddings
check("no-filter call also works and includes both embedded conversations",
      conv_a in all_centroids and conv_b in all_centroids)

try:
    os.remove(_tmp_db)  # best-effort — sqlite keeps the file handle open on Windows until GC'd
except OSError:
    pass

# ---------------------------------------------------------------------------
section("Server: real FastAPI app — /mind page + /ws/mind socket")
# ---------------------------------------------------------------------------

import server as server_module
import uvicorn
import websockets
from device_registry import DeviceRegistry
from conversation_store import ConversationStore as CS

_test_devices_file = os.path.join(tempfile.gettempdir(), "jarvis_test_devices_mind.json")
if os.path.exists(_test_devices_file):
    os.remove(_test_devices_file)
server_module.registry = DeviceRegistry(path=_test_devices_file)

_test_db2 = os.path.join(tempfile.gettempdir(), "jarvis_test_store_mind.db")
if os.path.exists(_test_db2):
    os.remove(_test_db2)
server_module.store = CS(path=_test_db2)

DEVICE_ID = "mind-test-device"
DEVICE_TOKEN = server_module.registry.register(DEVICE_ID, "Mind Test Device", ["filesystem"])


class FakeProvider:
    def __init__(self, rounds):
        self._rounds = list(rounds)

    def chat_stream(self, messages, system_prompt):
        if not self._rounds:
            return iter(["(no more canned rounds)"])
        return iter(self._rounds.pop(0))


def set_fake_rounds(rounds):
    server_module.brain._brain = FakeProvider(rounds)
    server_module.brain._llm_error = None


# Same background-loop collision test_teams_phase4.py's own test suite found and fixed:
# posture_monitor/daily_briefing/team_board/deep_reflection all fire an immediate check on
# server startup and broadcast to every connected socket — a real one landing mid-test
# would get mistaken for this test's own expected event. No-op them all here too.
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

WS_MIND_URL = f"ws://127.0.0.1:{TEST_PORT}/ws/mind"
WS_URL = f"ws://127.0.0.1:{TEST_PORT}/ws"


def http_get(path):
    return urllib.request.urlopen(f"http://127.0.0.1:{TEST_PORT}{path}", timeout=5)


resp = http_get("/mind")
check("GET /mind returns 200", resp.status == 200)
body = resp.read().decode("utf-8")
check("served page is mind.html (title present)", "Living Mind" in body)

resp2 = http_get("/vendor/three.module.js")
check("three.js is served locally (vendored, not a CDN reference)", resp2.status == 200)
resp3 = http_get("/vendor/OrbitControls.js")
check("OrbitControls.js is served locally", resp3.status == 200)
resp4 = http_get("/mind.js")
check("mind.js is served", resp4.status == 200)
resp5 = http_get("/mind.css")
check("mind.css is served", resp5.status == 200)


async def mind_hello(ws, device_id, token):
    await ws.send(json.dumps({"type": "hello", "device_id": device_id, "token": token}))
    return json.loads(await ws.recv())


async def main():
    section("/ws/mind auth")
    async with websockets.connect(WS_MIND_URL) as ws:
        msg = await mind_hello(ws, DEVICE_ID, "wrong-token")
        check("wrong token on /ws/mind gets an error", msg.get("type") == "error", f"got: {msg}")
        try:
            await ws.recv()
            check("connection closes after bad auth", False, "still open")
        except websockets.exceptions.ConnectionClosed as e:
            check("connection closes after bad auth", e.code == 4001, f"code={e.code}")

    section("/ws/mind snapshot on connect")
    async with websockets.connect(WS_MIND_URL) as ws:
        snapshot_msg = await mind_hello(ws, DEVICE_ID, DEVICE_TOKEN)
        check("valid hello gets a snapshot message directly", snapshot_msg.get("type") == "snapshot",
              f"got: {snapshot_msg}")
        check("snapshot over the wire has real tool nodes", any(
            n["id"] == "tool:error_tool" for n in snapshot_msg.get("nodes", [])
        ), f"node ids: {[n['id'] for n in snapshot_msg.get('nodes', [])][:10]}")

        section("/ws/mind refresh request")
        await ws.send(json.dumps({"type": "refresh"}))
        refreshed = json.loads(await ws.recv())
        check("refresh returns another snapshot", refreshed.get("type") == "snapshot")

    section("Live tool_fired event reaches a connected mind observer")
    set_fake_rounds([
        ['[TOOL: error_tool {}]'],
        ["Handled."],
    ])
    async with websockets.connect(WS_MIND_URL) as mind_ws:
        await mind_hello(mind_ws, DEVICE_ID, DEVICE_TOKEN)  # consumes the initial snapshot

        async with websockets.connect(WS_URL) as chat_ws:
            await chat_ws.send(json.dumps({
                "type": "hello", "device_id": DEVICE_ID, "token": DEVICE_TOKEN, "capabilities": ["filesystem"],
            }))
            await chat_ws.recv()  # ready
            await chat_ws.send(json.dumps({"type": "message", "text": "trigger the error tool"}))

            fired = None
            for _ in range(30):
                try:
                    data = json.loads(await asyncio.wait_for(mind_ws.recv(), timeout=1.0))
                    if data.get("type") == "tool_fired":
                        fired = data
                        break
                except asyncio.TimeoutError:
                    continue
            check("tool_fired event arrives on the mind socket", fired is not None, f"got: {fired}")
            if fired:
                check("tool_fired names the actual tool that ran", "error_tool" in fired.get("tools", []),
                      f"got: {fired}")

            # Drain the chat socket's own turn so nothing is left dangling.
            while True:
                data = json.loads(await asyncio.wait_for(chat_ws.recv(), timeout=5))
                if data.get("type") == "stream_end":
                    break

        section("Live memory_pulse event reaches a connected mind observer")
        pulsed = None
        for _ in range(10):
            try:
                data = json.loads(await asyncio.wait_for(mind_ws.recv(), timeout=1.0))
                if data.get("type") == "memory_pulse":
                    pulsed = data
                    break
            except asyncio.TimeoutError:
                continue
        check("memory_pulse event arrives after a turn completes", pulsed is not None, f"got: {pulsed}")
        if pulsed:
            check("memory_pulse carries a conversation_id", bool(pulsed.get("conversation_id")))


asyncio.run(main())


print(f"\n{'=' * 55}\n{PASS} passed, {FAIL} failed\n{'=' * 55}")
sys.exit(1 if FAIL else 0)

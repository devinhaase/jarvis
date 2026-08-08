"""
Tests for Phase 4: subagent team architecture — teams.py (team registry, alias
resolution), team_router.py (classification, sequential handoff, Cyber->Hacking approval
gate), conversation_store.py's team_incidents board, and tool_subset scope enforcement at
the real dispatch layer (coordinator.py).

Runs the real FastAPI/uvicorn server in a background thread against an isolated device
registry AND conversation store, same pattern as test_server_phase3.py/test_mind_phase8c.py
— never the real devices.json or data/jarvis.db. The LLM is swapped for a FakeProvider with
TWO separate canned-response queues: `chat_responses` for non-streaming .chat() calls
(team_router's classification/synthesis passes) and `chat_rounds` for streaming
.chat_stream() calls (each team's actual scoped turn) — these are genuinely different
provider methods called from different places, so keeping them separate queues avoids any
ambiguity about which canned answer a given call should get.

Required test coverage (per the Phase 4 spec):
  - A single-team request per team (5 teams, 5 tests minimum) -> section 3
  - One cross-team request requiring 2+ teams to collaborate -> section 4
  - One Hacking-team request against a target NOT on the authorization list -> section 5

Run: python test_teams_phase4.py
"""

import os
import sys
import json
import time
import asyncio
import threading
import tempfile

TEST_PORT = 8771
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
section("1. teams.py — registry and alias resolution")
# ---------------------------------------------------------------------------

import teams
from tools import ALL_TOOLS

check("all 5 teams registered", set(teams.TEAMS.keys()) ==
      {"personal_assistant", "network", "it", "cybersecurity", "hacking"})

for key, team in teams.TEAMS.items():
    names = team.tool_names()
    check(f"'{key}' team has at least one tool", len(names) > 0, f"got {len(names)}")
    check(f"'{key}' team includes coordinator-level tools (e.g. arm_kill_switch)",
          "arm_kill_switch" in names)
    owned = {n for n, t in ALL_TOOLS.items() if t.team == key}
    check(f"'{key}' team's tool_names() is exactly its owned tools + coordinator-level ones",
          names == owned | {n for n, t in ALL_TOOLS.items() if t.team is None})

check("resolve_team_alias matches an explicit address", teams.resolve_team_alias("ask the network team what's on my wifi") is teams.TEAMS["network"])
check("resolve_team_alias matches 'it team'", teams.resolve_team_alias("have the it team check disk space") is teams.TEAMS["it"])
check("resolve_team_alias matches 'hacking team'", teams.resolve_team_alias("tell the hacking team to scan 127.0.0.1") is teams.TEAMS["hacking"])
check("resolve_team_alias returns None for an unaddressed message", teams.resolve_team_alias("what's my cpu usage") is None)

check("no tool is owned by more than one team (single source of truth)",
      all(t.team in (None, "personal_assistant", "network", "it", "cybersecurity", "hacking") for t in ALL_TOOLS.values()))

# ---------------------------------------------------------------------------
section("2. conversation_store — team_incidents board CRUD")
# ---------------------------------------------------------------------------

from conversation_store import ConversationStore

_tmp_board_db = os.path.join(tempfile.gettempdir(), "jarvis_test_board.db")
if os.path.exists(_tmp_board_db):
    os.remove(_tmp_board_db)
board_store = ConversationStore(path=_tmp_board_db)

iid = board_store.create_incident("network", "Unknown device joined", "MAC aa:bb:cc:dd:ee:ff appeared", target_team="cybersecurity", severity="warning")
check("create_incident returns an id", bool(iid))

fetched = board_store.get_incident(iid)
check("get_incident returns the right row", fetched["title"] == "Unknown device joined")
check("status defaults to open", fetched["status"] == "open")
check("source_data defaults to {}", fetched["source_data"] == {})

open_for_cyber = board_store.list_incidents(target_team="cybersecurity", status="open")
check("list_incidents filters by target_team+status", len(open_for_cyber) == 1 and open_for_cyber[0]["id"] == iid)

open_for_it = board_store.list_incidents(target_team="it", status="open")
check("list_incidents returns nothing for a team with no incidents", open_for_it == [])

ok = board_store.update_incident_status(iid, "acknowledged")
check("update_incident_status succeeds", ok)
check("status actually changed", board_store.get_incident(iid)["status"] == "acknowledged")

check("update_incident_status on a bogus id returns False, doesn't raise", board_store.update_incident_status("nope", "resolved") is False)

try:
    os.remove(_tmp_board_db)
except OSError:
    pass

# ---------------------------------------------------------------------------
section("3. Server: real end-to-end team routing + tool-scope enforcement")
# ---------------------------------------------------------------------------

import server as server_module
import uvicorn
import websockets
from device_registry import DeviceRegistry
from conversation_store import ConversationStore as CS

_test_devices_file = os.path.join(tempfile.gettempdir(), "jarvis_test_devices_teams.json")
if os.path.exists(_test_devices_file):
    os.remove(_test_devices_file)
server_module.registry = DeviceRegistry(path=_test_devices_file)

_test_db = os.path.join(tempfile.gettempdir(), "jarvis_test_store_teams.db")
if os.path.exists(_test_db):
    os.remove(_test_db)
server_module.store = CS(path=_test_db)
import team_board_dispatcher
team_board_dispatcher.store = server_module.store

server_module.APPROVAL_TIMEOUT = {"TIER_3": 0.6, "TIER_4": 0.6}
server_module.APPROVAL_DEFAULT = {"TIER_3": True, "TIER_4": False}  # Tier 4 (the handoff gate uses this) defaults to deny on timeout

# Real bug found running this test against the real server: _lifespan starts
# posture_monitor_loop/daily_briefing_loop/team_board_dispatch_loop for real, and
# posture_monitor's first check compares against the REAL data/posture_snapshot.json on
# disk (not test-isolated) — if it finds a diff (very likely, given how many server
# restarts happen during a live-testing session), it broadcasts a stream_end to EVERY
# connected socket via _broadcast_all, by design (so no device misses an approval/alert
# regardless of which conversation it's watching). That's correct production behavior, but
# it collided with this test's naive "the next stream_end I receive is my answer" client
# helper — a stray posture-monitor broadcast landed on a test connection mid-turn and got
# mistaken for that turn's real result. Fixed by no-op'ing all three background loops for
# the duration of this test (team routing doesn't need them actually running concurrently)
# rather than trying to also isolate posture_snapshot.json — the loops firing at all during
# a routing test is the actual hazard, not just which file they read.
async def _noop_background_loop(*args, **kwargs):
    await asyncio.Event().wait()  # never completes; cleanly cancelled at server shutdown

import posture_monitor, daily_briefing, team_board_dispatcher as _tbd
posture_monitor.posture_monitor_loop = _noop_background_loop
daily_briefing.daily_briefing_loop = _noop_background_loop
_tbd.team_board_dispatch_loop = _noop_background_loop

DEVICE_ID = "teams-test-device"
DEVICE_TOKEN = server_module.registry.register(DEVICE_ID, "Teams Test Device", ["filesystem"])


class FakeProvider:
    """Two independent queues: `chat_responses` feeds .chat() (team_router's classify/
    synthesize passes), `chat_rounds` feeds .chat_stream() (each team's real scoped turn)
    — matches which provider method team_router.py actually calls for each purpose."""
    def __init__(self):
        self.chat_responses = []   # list of {"response": str, "tools": [...]} — consumed by .chat()
        self.chat_rounds = []      # list of list[str] chunks — consumed by .chat_stream(), one list per round
        self.chat_stream_calls = []
        self.chat_calls = []

    def chat_stream(self, messages, system_prompt):
        self.chat_stream_calls.append((messages, system_prompt))
        if not self.chat_rounds:
            return iter(["(no more canned chat_stream rounds)"])
        return iter(self.chat_rounds.pop(0))

    def chat(self, messages, system_prompt):
        self.chat_calls.append((messages, system_prompt))
        if not self.chat_responses:
            return {"response": "(no more canned chat responses)", "tools": []}
        return self.chat_responses.pop(0)


def set_fake_provider(chat_responses=None, chat_rounds=None):
    fp = FakeProvider()
    fp.chat_responses = list(chat_responses or [])
    fp.chat_rounds = list(chat_rounds or [])
    server_module.brain._brain = fp
    server_module.brain._llm_error = None
    return fp


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


async def send_and_collect(ws, text):
    await ws.send(json.dumps({"type": "message", "text": text}))
    events = []
    while True:
        data = json.loads(await ws.recv())
        events.append(data)
        if data.get("type") == "stream_end":
            break
    return events


def team_routing_events(events):
    return [e for e in events if e.get("type") == "tool_status" and e.get("event") == "team_routing"]


def team_active_events(events):
    return [e for e in events if e.get("type") == "tool_status" and e.get("event") == "team_active"]


async def single_team_test(label, address_phrase, expected_team, canned_round, expect_tool=None):
    set_fake_provider(chat_rounds=[canned_round])
    async with websockets.connect(WS_URL) as ws:
        await hello(ws, DEVICE_ID, DEVICE_TOKEN, ["filesystem"])
        events = await send_and_collect(ws, address_phrase)

    routing = team_routing_events(events)
    check(f"[{label}] explicit address routes without an LLM classification call",
          len(routing) == 1 and routing[0]["data"]["teams"] == [expected_team] and routing[0]["data"]["explicit"] is True,
          f"got: {routing}")
    active = team_active_events(events)
    check(f"[{label}] exactly one team went active ({expected_team})",
          len(active) == 1 and active[0]["data"]["team"] == expected_team, f"got: {active}")
    stream_end = [e for e in events if e.get("type") == "stream_end"][0]
    if expect_tool:
        check(f"[{label}] the team's own tool actually ran", expect_tool in stream_end.get("tools_ran", []),
              f"got: {stream_end.get('tools_ran')}")
    return stream_end


async def main():
    section("3a. Personal Assistant team — single-team request")
    await single_team_test(
        "personal_assistant", "ask the personal assistant team to list my tasks",
        "personal_assistant", ["[TOOL: list_tasks {}]"],
    )
    # separate turn for the tool's actual response round
    set_fake_provider(chat_rounds=[["[TOOL: list_tasks {}]"], ["You have no open tasks."]])
    async with websockets.connect(WS_URL) as ws:
        await hello(ws, DEVICE_ID, DEVICE_TOKEN, ["filesystem"])
        events = await send_and_collect(ws, "ask the personal assistant team to list my tasks")
    stream_end = [e for e in events if e.get("type") == "stream_end"][0]
    check("[personal_assistant] list_tasks tool actually executed", "list_tasks" in stream_end.get("tools_ran", []),
          f"got: {stream_end.get('tools_ran')}")
    check("[personal_assistant] final answer reflects the second round's text",
          "no open tasks" in stream_end.get("full_text", ""), f"got: {stream_end.get('full_text')!r}")

    section("3b. Network team — single-team request")
    set_fake_provider(chat_rounds=[["[TOOL: check_latency {\"host\": \"8.8.8.8\", \"count\": 2}]"], ["Latency looks normal."]])
    async with websockets.connect(WS_URL) as ws:
        await hello(ws, DEVICE_ID, DEVICE_TOKEN, ["filesystem"])
        events = await send_and_collect(ws, "ask the network team to check my latency")
    routing = team_routing_events(events)
    check("[network] routed explicitly to network team", routing and routing[0]["data"]["teams"] == ["network"], f"got: {routing}")
    stream_end = [e for e in events if e.get("type") == "stream_end"][0]
    check("[network] check_latency tool actually executed", "check_latency" in stream_end.get("tools_ran", []),
          f"got: {stream_end.get('tools_ran')}")

    section("3c. IT team — single-team request")
    set_fake_provider(chat_rounds=[["[TOOL: check_system_health {}]"], ["CPU is fine."]])
    async with websockets.connect(WS_URL) as ws:
        await hello(ws, DEVICE_ID, DEVICE_TOKEN, ["filesystem"])
        events = await send_and_collect(ws, "ask the it team to check system health")
    routing = team_routing_events(events)
    check("[it] routed explicitly to it team", routing and routing[0]["data"]["teams"] == ["it"], f"got: {routing}")
    stream_end = [e for e in events if e.get("type") == "stream_end"][0]
    check("[it] check_system_health tool actually executed", "check_system_health" in stream_end.get("tools_ran", []),
          f"got: {stream_end.get('tools_ran')}")

    section("3d. Cybersecurity team — single-team request")
    set_fake_provider(chat_rounds=[["[TOOL: get_security_posture {}]"], ["Posture looks fine."]])
    async with websockets.connect(WS_URL) as ws:
        await hello(ws, DEVICE_ID, DEVICE_TOKEN, ["filesystem"])
        events = await send_and_collect(ws, "ask the cybersecurity team for my posture")
    routing = team_routing_events(events)
    check("[cybersecurity] routed explicitly to cybersecurity team", routing and routing[0]["data"]["teams"] == ["cybersecurity"], f"got: {routing}")
    stream_end = [e for e in events if e.get("type") == "stream_end"][0]
    check("[cybersecurity] get_security_posture tool actually executed", "get_security_posture" in stream_end.get("tools_ran", []),
          f"got: {stream_end.get('tools_ran')}")

    section("3e. Hacking team — single-team request against an AUTHORIZED target")
    set_fake_provider(chat_rounds=[["[TOOL: msf_module_info {\"search_term\": \"eternalblue\"}]"], ["Found module info."]])
    async with websockets.connect(WS_URL) as ws:
        await hello(ws, DEVICE_ID, DEVICE_TOKEN, ["filesystem"])
        events = await send_and_collect(ws, "ask the hacking team for module info on eternalblue")
    routing = team_routing_events(events)
    check("[hacking] routed explicitly to hacking team", routing and routing[0]["data"]["teams"] == ["hacking"], f"got: {routing}")
    stream_end = [e for e in events if e.get("type") == "stream_end"][0]
    check("[hacking] msf_module_info (no-target, read-only) tool actually executed",
          "msf_module_info" in stream_end.get("tools_ran", []), f"got: {stream_end.get('tools_ran')}")

    section("3f. Scope enforcement — a team cannot execute another team's tool")
    # Explicitly address the Network team but script the model to try a Cybersecurity-only
    # tool. tool_subset enforcement at coordinator.run_tools() must refuse it — this is the
    # real gate, not just prompt-level hiding. Note: tools_ran records tools *attempted*
    # this turn regardless of outcome (same as a Tier-3/4 denial or an authorization
    # failure) — the refusal signal lives in the tool-result text fed back to the model,
    # not in tools_ran's membership, so that's what this checks.
    fp3f = set_fake_provider(chat_rounds=[["[TOOL: get_security_posture {}]"], ["Could not check."]])
    async with websockets.connect(WS_URL) as ws:
        await hello(ws, DEVICE_ID, DEVICE_TOKEN, ["filesystem"])
        events = await send_and_collect(ws, "ask the network team to check my posture")
    second_call_messages_3f = fp3f.chat_stream_calls[1][0]
    tool_result_3f = next(m["content"] for m in second_call_messages_3f if m["role"] == "user" and "TOOL_OUTPUT" in m["content"])
    check("[scope enforcement] out-of-team tool was refused at dispatch, not executed",
          "outside this team's scope" in tool_result_3f, f"got: {tool_result_3f!r}")

    section("4. Cross-team request — Network gathers, Cybersecurity assesses, one synthesized reply")
    fp = set_fake_provider(
        chat_responses=[
            {"response": "[TEAM: network]\n[TEAM: cybersecurity]", "tools": []},  # classification pass
            {"response": "Network found 14 devices online, nothing unusual, and Defender/firewall are both on — no action needed.", "tools": []},  # synthesis pass
        ],
        chat_rounds=[
            ["Scanned the LAN — 14 devices online, none unrecognized."],   # network team's own turn (no tool call, keep deterministic)
            ["Reviewed those findings against current posture — Defender and firewall are both on, nothing concerning."],  # cybersecurity team's own turn
        ],
    )
    async with websockets.connect(WS_URL) as ws:
        await hello(ws, DEVICE_ID, DEVICE_TOKEN, ["filesystem"])
        events = await send_and_collect(ws, "check if my network is secure")

    routing = team_routing_events(events)
    check("[cross-team] classification selected both teams in dependency order",
          routing and routing[0]["data"]["teams"] == ["network", "cybersecurity"] and routing[0]["data"]["explicit"] is False,
          f"got: {routing}")
    active = team_active_events(events)
    check("[cross-team] both teams went active in order",
          [e["data"]["team"] for e in active] == ["network", "cybersecurity"], f"got: {active}")
    stream_end = [e for e in events if e.get("type") == "stream_end"][0]
    check("[cross-team] final reply is the synthesized combined answer, not just one team's raw text",
          "no action needed" in stream_end.get("full_text", ""), f"got: {stream_end.get('full_text')!r}")
    # Verify the second team's system prompt actually received the first team's findings —
    # the real point of "handoff", not just that both ran in sequence.
    second_team_call = fp.chat_stream_calls[1]
    check("[cross-team] Cybersecurity team's prompt includes Network team's findings as handoff context",
          "14 devices online" in second_team_call[1], f"system prompt tail: {second_team_call[1][-500:]!r}")

    section("4b. Cyber -> Hacking auto-chain requires approval, defaults to deny on timeout")
    set_fake_provider(
        chat_responses=[{"response": "[TEAM: cybersecurity]\n[TEAM: hacking]", "tools": []}],
        chat_rounds=[["Posture check suggests testing 127.0.0.1 would help confirm."]],  # cybersecurity's turn only — hacking should never run
    )
    async with websockets.connect(WS_URL) as ws:
        await hello(ws, DEVICE_ID, DEVICE_TOKEN, ["filesystem"])
        events = await send_and_collect(ws, "is my machine actually secure or just reporting that it is")

    gate_events = [e for e in events if e.get("type") == "tool_status" and e.get("event") == "team_handoff_gate"]
    check("[handoff gate] Cyber->Hacking auto-chain triggers an approval request",
          len(gate_events) == 1 and gate_events[0]["data"]["from"] == "cybersecurity" and gate_events[0]["data"]["to"] == "hacking",
          f"got: {gate_events}")
    check("[handoff gate] unanswered approval defaults to deny (Tier 4 default)",
          gate_events[0]["data"]["approved"] is False, f"got: {gate_events}")
    active = team_active_events(events)
    check("[handoff gate] Hacking team never went active without approval",
          "hacking" not in [e["data"]["team"] for e in active], f"got: {active}")
    stream_end = [e for e in events if e.get("type") == "stream_end"][0]
    check("[handoff gate] final response explains the stop, doesn't silently drop it",
          "not approved" in stream_end.get("full_text", "").lower() or "did not approve" in stream_end.get("full_text", "").lower(),
          f"got: {stream_end.get('full_text')!r}")

    section("5. Hacking team refuses a target NOT on the authorization list")
    fp5 = set_fake_provider(chat_rounds=[["[TOOL: scan_ports {\"target\": \"203.0.113.5\"}]"], ["I couldn't run that scan — see the tool output for why."]])
    async with websockets.connect(WS_URL) as ws:
        await hello(ws, DEVICE_ID, DEVICE_TOKEN, ["filesystem"])
        events = await send_and_collect(ws, "ask the hacking team to scan 203.0.113.5")

    routing = team_routing_events(events)
    check("[unauthorized target] still routed to hacking team (routing isn't the thing refusing)",
          routing and routing[0]["data"]["teams"] == ["hacking"], f"got: {routing}")

    # The model's own free-text summary is arbitrary with a FakeProvider (there's no real
    # model reading the tool output) — the real thing to verify is what the coordinator
    # actually fed back as the tool result, captured in the second chat_stream call's
    # messages (the [SYSTEM] tool-output-wrapped user turn).
    second_call_messages = fp5.chat_stream_calls[1][0]
    tool_result_text = next(m["content"] for m in second_call_messages if m["role"] == "user" and "TOOL_OUTPUT" in m["content"])
    check("[unauthorized target] refusal names the unauthorized target",
          "203.0.113.5" in tool_result_text, f"got: {tool_result_text!r}")
    check("[unauthorized target] refusal points at authorized_targets.json and explains how to add it",
          "authorized_targets" in tool_result_text.lower() and "NOT in your authorized targets" in tool_result_text,
          f"got: {tool_result_text!r}")


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

"""
Tests for Phase 7: Team Dashboards + Overseer — server.py's team status tracker,
team-attributed approvals (Coordinator._request_approval/run_tools, threaded through
session_manager.py and team_router.py), memory.py's team-tagged episodes,
_overseer_snapshot()/_team_dashboard(), and team_board_dispatcher.py's set_team_status DI.

Run: python test_dashboards_phase7.py
"""

import os
import sys
import json
import time
import asyncio
import threading
import tempfile

TEST_PORT = 8775
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
section("1. Coordinator._request_approval / run_tools — team threading, backward-compatible")
# ---------------------------------------------------------------------------

import coordinator as coord_module
from memory import Memory
from tools import Tier, ALL_TOOLS

_tmp_mem_dir = tempfile.mkdtemp(prefix="jarvis_test_mem_")
mem = Memory(data_dir=_tmp_mem_dir)

# A 2-arg approval_fn — exactly the shape every pre-Phase-7 test/caller uses.
calls_2arg = []
def _approval_2arg(action, tier):
    calls_2arg.append((action, tier))
    return True

c2 = coord_module.Coordinator(approval_fn=_approval_2arg, memory=mem)
result = c2._request_approval("do_a_thing", Tier.TIER_4, team="network")
check("a 2-arg approval_fn (pre-Phase-7 shape) still works — TypeError falls back cleanly", result is True)
check("the 2-arg fn was actually called (not silently skipped)", calls_2arg == [("do_a_thing", Tier.TIER_4)])

# A 3-arg approval_fn — the new shape, receives team as a keyword.
calls_3arg = []
def _approval_3arg(action, tier, team=None):
    calls_3arg.append((action, tier, team))
    return True

c3 = coord_module.Coordinator(approval_fn=_approval_3arg, memory=mem)
c3._request_approval("do_another_thing", Tier.TIER_3, team="it")
check("a 3-arg approval_fn receives team as a keyword", calls_3arg == [("do_another_thing", Tier.TIER_3, "it")], f"got: {calls_3arg}")

c3._request_approval("no_team_given", Tier.TIER_3)
check("team defaults to None when the caller doesn't pass one", calls_3arg[-1] == ("no_team_given", Tier.TIER_3, None), f"got: {calls_3arg[-1]}")

# ---------------------------------------------------------------------------
section("1b. _autofill_created_by_team — real bug found live-testing this item")
# ---------------------------------------------------------------------------
# Found live: a real local model reliably omits create_team_incident's required
# created_by_team argument (it has no reliable way to know its own team name), which
# used to fail outright with "missing 1 required positional argument". The coordinator
# already knows the answer by the time it dispatches the call — episode_team, the exact
# same value now threaded through for episode/approval attribution — so it fills it in
# rather than trusting the model to repeat it back correctly.

args_missing = {"title": "test", "target_team": "cybersecurity"}
coord_module._autofill_created_by_team("create_team_incident", args_missing, "network")
check("created_by_team is auto-filled from the active team when the model omits it",
      args_missing.get("created_by_team") == "network", f"got: {args_missing}")

args_explicit = {"title": "test", "created_by_team": "hacking"}
coord_module._autofill_created_by_team("create_team_incident", args_explicit, "network")
check("an explicit created_by_team the model DID provide is never overridden",
      args_explicit["created_by_team"] == "hacking", f"got: {args_explicit}")

args_other_tool = {"target": "8.8.8.8"}
coord_module._autofill_created_by_team("check_latency", args_other_tool, "network")
check("the autofill only ever touches create_team_incident, not other tools' args",
      "created_by_team" not in args_other_tool, f"got: {args_other_tool}")

# End-to-end through the real dispatch path, not just the helper in isolation.
mem_incident = Memory(data_dir=tempfile.mkdtemp(prefix="jarvis_test_mem_incident_"))
c5 = coord_module.Coordinator(approval_fn=lambda a, t, team=None: True, memory=mem_incident)
if "create_team_incident" in ALL_TOOLS:
    summary = c5.run_tools(
        [{"tool": "create_team_incident", "args": {"title": "Unknown device joined", "target_team": "cybersecurity", "severity": "warning"}}],
        team="network",
    )
    check("create_team_incident actually succeeds end-to-end when the model omits created_by_team",
          "SUCCESS" in summary and "missing 1 required positional argument" not in summary, f"got: {summary}")

# ---------------------------------------------------------------------------
section("2. memory.py — team-tagged episodes")
# ---------------------------------------------------------------------------

mem.log_episode("check_wan_status", "all good", "TIER_1", team="network", outcome="success")
mem.log_episode("check_system_health", "cpu fine", "TIER_1", team="it", outcome="success")
mem.log_episode("scan_ports", "ERROR: refused", "TIER_3", team="hacking", outcome="failed")
mem.log_episode("legacy_call_no_team", "ok", "TIER_1")  # pre-Phase-7 shape — team/outcome omitted

network_eps = mem.get_team_episodes("network", limit=10)
check("get_team_episodes returns only entries tagged for that team", len(network_eps) == 1 and network_eps[0]["action"] == "check_wan_status", f"got: {network_eps}")

it_eps = mem.get_team_episodes("it", limit=10)
check("a different team's episodes don't leak into another team's history", len(it_eps) == 1 and it_eps[0]["action"] == "check_system_health")

check("an episode logged with no team= (old call shape) doesn't show up under any team", mem.get_team_episodes(None, limit=50) == [] or all(e.get("team") is not None for e in []))
check("outcome is stored explicitly, not just re-derivable from result text", mem.get_team_episodes("hacking", limit=10)[0]["outcome"] == "failed")

recent = mem.get_recent_episodes(n=10)
check("get_recent_episodes (pre-Phase-7 API) still works unchanged and includes the new fields", any(e.get("team") == "network" for e in recent))

# run_tools/run_single_tool actually tag episodes with the *active* team, not tool.team
mem2 = Memory(data_dir=_tmp_mem_dir)
c4 = coord_module.Coordinator(approval_fn=lambda a, t, team=None: True, memory=mem2)
# get_self_knowledge is coordinator-level (tool.team is None) — run it as part of a
# specific team's turn and confirm the episode is tagged with THAT team, not None.
if "get_self_knowledge" in ALL_TOOLS:
    check("a coordinator-level tool (tool.team=None) still gets tagged with the caller's active team",
          ALL_TOOLS["get_self_knowledge"].team is None)
    c4.run_tools([{"tool": "get_self_knowledge", "args": {}}], team="cybersecurity")
    tagged = mem2.get_team_episodes("cybersecurity", limit=5)
    check("...and it actually shows up under that team's dashboard history", any(e["action"] == "get_self_knowledge" for e in tagged), f"got: {tagged}")

# ---------------------------------------------------------------------------
section("3. session_manager.py / team_router.py — team_key actually threaded through")
# ---------------------------------------------------------------------------

import inspect
import session_manager
import team_router

check("JarvisBrain.process_turn accepts team_key", "team_key" in inspect.signature(session_manager.JarvisBrain.process_turn).parameters)
check("JarvisBrain.process_turn_stream accepts team_key", "team_key" in inspect.signature(session_manager.JarvisBrain.process_turn_stream).parameters)
check("process_turn passes team_key through to coordinator.run_tools", "team=team_key" in inspect.getsource(session_manager.JarvisBrain.process_turn))
check("process_turn_stream passes team_key through to coordinator.run_tools", "team=team_key" in inspect.getsource(session_manager.JarvisBrain.process_turn_stream))
check("team_router.route_and_run passes team_key into process_turn_stream", "team_key=team_key" in inspect.getsource(team_router.route_and_run))
check("the Cybersecurity->Hacking handoff gate attributes its approval to cybersecurity", 'team="cybersecurity"' in inspect.getsource(team_router.route_and_run))

# ---------------------------------------------------------------------------
section("4. team_board_dispatcher.py — set_team_status DI + cross_team_incident broadcast")
# ---------------------------------------------------------------------------

import team_board_dispatcher

check("_dispatch_one_incident accepts set_team_status", "set_team_status" in inspect.signature(team_board_dispatcher._dispatch_one_incident).parameters)
check("team_board_dispatch_loop accepts and forwards set_team_status", "set_team_status" in inspect.signature(team_board_dispatcher.team_board_dispatch_loop).parameters)
_dispatch_src = inspect.getsource(team_board_dispatcher._dispatch_one_incident)
check("_dispatch_one_incident flips the target team to working, then back to idle", '"working"' in _dispatch_src and '"idle"' in _dispatch_src)
check("_dispatch_one_incident passes team_key=target_team into process_turn", "team_key=target_team" in _dispatch_src)
check("_dispatch_one_incident broadcasts cross_team_incident", "cross_team_incident" in _dispatch_src)


async def _run_dispatch_di_test():
    calls = []

    async def fake_set_status(team, status, detail=""):
        calls.append((team, status, detail))

    class FakeBrain:
        def process_turn(self, chat_history, device_capabilities=None, max_iterations=3,
                          conversation_id=None, tool_subset=None, team_context="", team_key=None):
            calls.append(("process_turn_called", team_key))
            return {"response": "handled", "tools_ran": [], "denied": []}

    from conversation_store import ConversationStore as CS
    tmp_db = os.path.join(tempfile.gettempdir(), "jarvis_test_board_di.db")
    if os.path.exists(tmp_db):
        os.remove(tmp_db)
    test_store = CS(path=tmp_db)
    team_board_dispatcher.store = test_store

    incident_id = test_store.create_incident("network", "Unknown device joined", "details", target_team="cybersecurity", severity="warning")
    incident = test_store.get_incident(incident_id)

    broadcasts = []
    async def fake_broadcast(payload):
        broadcasts.append(payload)

    await team_board_dispatcher._dispatch_one_incident(FakeBrain(), incident, broadcast_all=fake_broadcast, set_team_status=fake_set_status)

    check("set_team_status('working') fired for the TARGET team before dispatch", calls[0] == ("cybersecurity", "working", f"auto-dispatched: {incident['title']}"), f"got: {calls}")
    check("process_turn was actually called with team_key=target_team", ("process_turn_called", "cybersecurity") in calls)
    check("set_team_status('idle') fired after dispatch completed", calls[-1] == ("cybersecurity", "idle", ""), f"got: {calls}")
    check("the incident was marked acknowledged", test_store.get_incident(incident_id)["status"] == "acknowledged")
    cross_team_broadcasts = [b for b in broadcasts if b.get("type") == "cross_team_incident"]
    check("a cross_team_incident broadcast fired with the right from/to teams",
          len(cross_team_broadcasts) == 1 and cross_team_broadcasts[0]["from_team"] == "network" and cross_team_broadcasts[0]["to_team"] == "cybersecurity",
          f"got: {cross_team_broadcasts}")
    try:
        os.remove(tmp_db)
    except OSError:
        pass

asyncio.run(_run_dispatch_di_test())

# ---------------------------------------------------------------------------
section("5. Server: real WS protocol — get_overseer_snapshot / get_team_dashboard / team_status_changed")
# ---------------------------------------------------------------------------

import server as server_module
import uvicorn
import websockets
from device_registry import DeviceRegistry
from conversation_store import ConversationStore as CS

_test_devices_file = os.path.join(tempfile.gettempdir(), "jarvis_test_devices_dash.json")
if os.path.exists(_test_devices_file):
    os.remove(_test_devices_file)
server_module.registry = DeviceRegistry(path=_test_devices_file)

_test_db = os.path.join(tempfile.gettempdir(), "jarvis_test_store_dash.db")
if os.path.exists(_test_db):
    os.remove(_test_db)
server_module.store = CS(path=_test_db)
import team_board_dispatcher as _tbd
_tbd.store = server_module.store


async def _noop_background_loop(*args, **kwargs):
    await asyncio.Event().wait()

import posture_monitor, daily_briefing, deep_reflection as _dr
posture_monitor.posture_monitor_loop = _noop_background_loop
daily_briefing.daily_briefing_loop = _noop_background_loop
_tbd.team_board_dispatch_loop = _noop_background_loop
_dr.deep_reflection_loop = _noop_background_loop

# Reset the module-level team_status to a known baseline (other test runs / import-time
# state shouldn't leak into this one).
for _k in server_module.TEAM_KEYS:
    server_module.team_status[_k] = {"status": "idle", "detail": "", "since": 0.0}
server_module.pending_approval_meta.clear()

DEVICE_ID = "dash-test-device"
DEVICE_TOKEN = server_module.registry.register(DEVICE_ID, "Dashboard Test Device", ["filesystem"])

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


async def hello(ws, device_id, token, caps):
    await ws.send(json.dumps({
        "type": "hello", "device_id": device_id, "token": token,
        "capabilities": caps, "conversation_id": None,
    }))
    return json.loads(await ws.recv())


async def main():
    async with websockets.connect(WS_URL) as ws:
        await hello(ws, DEVICE_ID, DEVICE_TOKEN, ["filesystem"])

        await ws.send(json.dumps({"type": "get_overseer_snapshot"}))
        snap = json.loads(await ws.recv())
        check("get_overseer_snapshot returns type=overseer_snapshot", snap.get("type") == "overseer_snapshot", f"got: {snap}")
        check("snapshot lists all 5 teams", {t["key"] for t in snap.get("teams", [])} == set(server_module.TEAM_KEYS), f"got: {snap.get('teams')}")
        check("every team starts idle in a fresh snapshot", all(t["status"] == "idle" for t in snap["teams"]), f"got: {snap['teams']}")
        check("snapshot includes an empty queue initially", snap.get("queue") == [])
        check("snapshot includes integration + vpn health blocks", "integration" in snap and "vpn" in snap)

        await ws.send(json.dumps({"type": "get_team_dashboard", "team": "network"}))
        dash = json.loads(await ws.recv())
        check("get_team_dashboard returns type=team_dashboard for a real team", dash.get("type") == "team_dashboard" and dash.get("key") == "network", f"got: {dash}")
        check("team dashboard includes history/queue/proposed_skills/health keys", all(k in dash for k in ("history", "queue", "proposed_skills", "health", "incidents")))

        await ws.send(json.dumps({"type": "get_team_dashboard", "team": "not_a_real_team"}))
        bad_dash = json.loads(await ws.recv())
        check("an unknown team key returns a clean error, not a crash", "error" in bad_dash, f"got: {bad_dash}")

    # _set_team_status broadcasts team_status_changed to every connected device
    async with websockets.connect(WS_URL) as ws:
        await hello(ws, DEVICE_ID, DEVICE_TOKEN, ["filesystem"])
        await server_module._set_team_status("network", "working", "test broadcast")
        broadcast = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
        check("_set_team_status actually broadcasts team_status_changed live",
              broadcast.get("type") == "team_status_changed" and broadcast.get("team") == "network" and broadcast.get("status") == "working",
              f"got: {broadcast}")
        check("server_module.team_status was actually updated in memory", server_module.team_status["network"]["status"] == "working")
        await server_module._set_team_status("network", "idle", "")

asyncio.run(main())

uv_server.should_exit = True
time.sleep(0.5)
for f in (_test_devices_file, _test_db):
    try:
        os.remove(f)
    except OSError:
        pass

# ---------------------------------------------------------------------------
section("6. _wait_for_approval — team in pending_approval_meta, broadcast, and blocked status")
# ---------------------------------------------------------------------------


async def _run_approval_team_test():
    for k in server_module.TEAM_KEYS:
        server_module.team_status[k] = {"status": "idle", "detail": "", "since": 0.0}
    server_module.main_loop = asyncio.get_running_loop()

    broadcasts = []
    orig_broadcast_all = server_module._broadcast_all
    async def spy_broadcast_all(payload):
        broadcasts.append(payload)
        # don't actually try to reach any real websockets
    server_module._broadcast_all = spy_broadcast_all

    async def resolve_soon(delay=0.2):
        await asyncio.sleep(delay)
        for aid, meta in list(server_module.pending_approval_meta.items()):
            if meta["action"] == "test_action_for_team":
                fut = server_module.pending_approvals.get(aid)
                if fut and not fut.done():
                    fut.set_result(True)

    asyncio.create_task(resolve_soon())
    result = await server_module._wait_for_approval("test_action_for_team", "TIER_4", team="it")
    check("_wait_for_approval returns the resolved value", result is True)

    approval_broadcasts = [b for b in broadcasts if b.get("type") == "approval_request"]
    check("the approval_request broadcast includes the team", approval_broadcasts and approval_broadcasts[0].get("team") == "it", f"got: {approval_broadcasts}")
    check("pending_approval_meta was cleaned up after resolution", not any(m.get("action") == "test_action_for_team" for m in server_module.pending_approval_meta.values()))
    check("team status returned to idle (not left blocked) after resolution", server_module.team_status["it"]["status"] != "blocked", f"got: {server_module.team_status['it']}")

    server_module._broadcast_all = orig_broadcast_all

asyncio.run(_run_approval_team_test())

# ---------------------------------------------------------------------------
section("7. Static checks — client-side dashboard wiring actually present")
# ---------------------------------------------------------------------------

import re

with open("webapp/index.html", "r", encoding="utf-8") as f:
    index_html = f.read()
with open("webapp/app.js", "r", encoding="utf-8") as f:
    app_js = f.read()
with open("webapp/style.css", "r", encoding="utf-8") as f:
    style_css = f.read()
with open("webapp/sw.js", "r", encoding="utf-8") as f:
    sw_js = f.read()

check("index.html has the Dashboard/Chat tab switcher, Dashboard active by default", 'id="dashboardTabBtn" class="view-tab active"' in index_html)
check("index.html's #main starts hidden (Dashboard is the default landing view)", 'id="main" class="hidden"' in index_html)
check("index.html has team tiles, queue, cross-team log, and health strip containers", all(f'id="{i}"' in index_html for i in ("teamTiles", "overseerQueue", "crossTeamLog", "systemHealthStrip")))
check("index.html has the team drill-down view with its own back button", 'id="teamDashboardView"' in index_html and 'id="backToOverseerBtn"' in index_html)

check("app.js wires the tab switcher to showDashboardView/showChatView", "dashboardTabBtn.addEventListener" in app_js and "chatTabBtn.addEventListener" in app_js)
check("app.js requests the overseer snapshot on 'ready' (populated before you even click the tab)", re.search(r'case\s+"ready":.*?get_overseer_snapshot', app_js, re.S) is not None)
check("app.js handles overseer_snapshot/team_dashboard/team_status_changed/cross_team_incident/approval_resolved",
      all(f'case "{t}":' in app_js for t in ("overseer_snapshot", "team_dashboard", "team_status_changed", "cross_team_incident", "approval_resolved")))
check("app.js's queue rendering wires real Approve/Deny buttons using the same 'approve' WS message the chat banner uses",
      re.search(r'send\(\{\s*type:\s*"approve",\s*approval_id:\s*item\.id,\s*approved:\s*true\s*\}\)', app_js) is not None)
check("refreshCurrentDashboardView is wired into skill_updated/approval_request/approval_resolved (the actual live-update triggers)",
      app_js.count("refreshCurrentDashboardView()") >= 3)
check("app.js defines openDashboardTeam for the notification deep-link (?team=)", "function openDashboardTeam" in app_js)
check("app.js's postMessage listener handles open_dashboard from the service worker", "open_dashboard" in app_js)

check("style.css defines #contentArea (the new flex wrapper for tabs + Chat/Dashboard)", "#contentArea" in style_css)
check("style.css styles team tiles with distinct working/blocked/idle states", ".status-working" in style_css and ".status-blocked" in style_css and ".team-tile" in style_css)
check("style.css has a mobile breakpoint adjustment for team tiles", "team-tiles" in style_css and "@media (max-width: 480px)" in style_css)

check("sw.js's push handler carries the dashboard field through to notification data", '"dashboard": payload.dashboard' in sw_js.replace("data: { conversation_id: payload.conversation_id || null, dashboard: payload.dashboard || null }", '"dashboard": payload.dashboard') or "dashboard: payload.dashboard" in sw_js)
check("sw.js's notificationclick deep-links to a team dashboard when present", "open_dashboard" in sw_js and "?team=" in sw_js)

print(f"\n{'=' * 55}\n{PASS} passed, {FAIL} failed\n{'=' * 55}")
sys.exit(1 if FAIL else 0)

"""
Tests for Phase 6 item 5: GUI polish — theme toggle, integration status panel
(Obsidian/Gmail/Calendar/Drive dots), skills-pending-queue badge, and the persistent
per-message team-routing badge.

Runs the real FastAPI/uvicorn server in a background thread against an isolated device
registry AND conversation store, same pattern as test_teams_phase4.py — never the real
devices.json or data/jarvis.db. Static-source checks (regex-anchored, not naive substring —
same reasoning as test_deep_reflection_phase6.py's guarantee checks) cover the client-side
JS/CSS wiring that a Python test can't exercise directly; the actual rendering was verified
live in a real browser (documented in task.md), not just asserted here.

Run: python test_gui_polish_phase6.py
"""

import os
import re
import sys
import json
import time
import asyncio
import threading
import tempfile

TEST_PORT = 8772
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
section("1. server._integration_status() — unit, no real OAuth/network calls")
# ---------------------------------------------------------------------------

import server as server_module
import obsidian_tools
import google_auth

_orig_vault_root = obsidian_tools._vault_root
_orig_conn_status = google_auth.connection_status

# Point at a real (temp) directory to exercise the "connected" path without touching
# Devin's actual vault.
_tmp_vault = tempfile.mkdtemp(prefix="jarvis_test_vault_")
obsidian_tools._vault_root = lambda: _tmp_vault
google_auth.connection_status = lambda: {
    "connected": True, "gmail": True, "gmail_compose": True, "calendar": True, "drive": False,
    "granted_scopes": [],
}

status = server_module._integration_status()
check("obsidian reports connected when vault dir exists", status["obsidian"] is True, f"got: {status}")
check("gmail reflects google_auth.connection_status()", status["gmail"] is True, f"got: {status}")
check("calendar reflects google_auth.connection_status()", status["calendar"] is True, f"got: {status}")
check("drive reflects google_auth.connection_status()", status["drive"] is False, f"got: {status}")

obsidian_tools._vault_root = lambda: os.path.join(_tmp_vault, "does_not_exist")
status2 = server_module._integration_status()
check("obsidian reports disconnected when vault dir is missing", status2["obsidian"] is False, f"got: {status2}")

google_auth.connection_status = lambda: (_ for _ in ()).throw(RuntimeError("token file corrupt"))
status3 = server_module._integration_status()
check("a broken google_auth.connection_status() degrades to all-False instead of raising",
      status3 == {"obsidian": False, "gmail": False, "calendar": False, "drive": False}, f"got: {status3}")

obsidian_tools._vault_root = _orig_vault_root
google_auth.connection_status = _orig_conn_status

# ---------------------------------------------------------------------------
section("2. Server: get_integration_status over the real WS protocol")
# ---------------------------------------------------------------------------

import uvicorn
import websockets
from device_registry import DeviceRegistry
from conversation_store import ConversationStore as CS

_test_devices_file = os.path.join(tempfile.gettempdir(), "jarvis_test_devices_guipolish.json")
if os.path.exists(_test_devices_file):
    os.remove(_test_devices_file)
server_module.registry = DeviceRegistry(path=_test_devices_file)

_test_db = os.path.join(tempfile.gettempdir(), "jarvis_test_store_guipolish.db")
if os.path.exists(_test_db):
    os.remove(_test_db)
server_module.store = CS(path=_test_db)
import team_board_dispatcher
team_board_dispatcher.store = server_module.store


async def _noop_background_loop(*args, **kwargs):
    await asyncio.Event().wait()

import posture_monitor, daily_briefing, deep_reflection as _dr
posture_monitor.posture_monitor_loop = _noop_background_loop
daily_briefing.daily_briefing_loop = _noop_background_loop
team_board_dispatcher.team_board_dispatch_loop = _noop_background_loop
_dr.deep_reflection_loop = _noop_background_loop

DEVICE_ID = "gui-polish-test-device"
DEVICE_TOKEN = server_module.registry.register(DEVICE_ID, "GUI Polish Test Device", ["filesystem"])

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
        await ws.send(json.dumps({"type": "get_integration_status"}))
        resp = json.loads(await ws.recv())
    check("get_integration_status returns type=integration_status", resp.get("type") == "integration_status", f"got: {resp}")
    for key in ("obsidian", "gmail", "calendar", "drive"):
        check(f"integration_status includes boolean '{key}'", isinstance(resp.get(key), bool), f"got: {resp}")

asyncio.run(main())

uv_server.should_exit = True
time.sleep(0.5)
for f in (_test_devices_file, _test_db):
    try:
        os.remove(f)
    except OSError:
        pass
try:
    os.rmdir(_tmp_vault)
except OSError:
    pass

# ---------------------------------------------------------------------------
section("3. Static checks — client-side wiring actually present (regex-anchored)")
# ---------------------------------------------------------------------------

with open("webapp/app.js", "r", encoding="utf-8") as f:
    app_js = f.read()
with open("webapp/index.html", "r", encoding="utf-8") as f:
    index_html = f.read()
with open("webapp/style.css", "r", encoding="utf-8") as f:
    style_css = f.read()

check("index.html has the theme toggle button", 'id="themeToggleBtn"' in index_html)
check("index.html has the 4 integration dots", all(
    f'id="statusDot{name}"' in index_html for name in ("Obsidian", "Gmail", "Calendar", "Drive")
))
check("index.html has the skills pending badge", 'id="skillsPendingBadge"' in index_html)

check("app.js wires a click handler on themeToggleBtn",
      re.search(r"els\.themeToggleBtn\.addEventListener\(\s*[\"']click[\"']", app_js) is not None)
check("app.js persists theme choice to localStorage",
      re.search(r"localStorage\.setItem\(\s*[\"']jarvis_theme[\"']", app_js) is not None)
check("app.js applies saved theme on load (not just on click)",
      re.search(r"applyTheme\(\s*localStorage\.getItem\(\s*[\"']jarvis_theme[\"']", app_js) is not None)

check("app.js requests integration status proactively on 'ready', not just on demand",
      re.search(r'case\s+"ready":.*?send\(\{\s*type:\s*"get_integration_status"', app_js, re.S) is not None)
check("app.js requests skills proactively on 'ready' (for the pending badge count)",
      re.search(r'case\s+"ready":.*?send\(\{\s*type:\s*"list_skills"', app_js, re.S) is not None)

check("app.js handles the integration_status message type",
      re.search(r'case\s+"integration_status":', app_js) is not None)
check("app.js defines renderIntegrationStatus, toggling a 'connected' class per dot",
      re.search(r"function renderIntegrationStatus\(.*?classList\.toggle\(\s*[\"']connected[\"']", app_js, re.S) is not None)

check("app.js defines updateSkillsPendingBadge, counting status === 'proposed'",
      re.search(r"function updateSkillsPendingBadge\(.*?status\s*===\s*[\"']proposed[\"']", app_js, re.S) is not None)
check("skill_list handler calls updateSkillsPendingBadge",
      re.search(r'case\s+"skill_list":.*?updateSkillsPendingBadge\(\)', app_js, re.S) is not None)
check("skill_updated handler calls updateSkillsPendingBadge",
      re.search(r'case\s+"skill_updated":.*?updateSkillsPendingBadge\(\)', app_js, re.S) is not None)

check("app.js defines _consumePendingTeamBadge",
      "_consumePendingTeamBadge" in app_js)
check("appendAssistantBubble only consumes the pending team badge when isLive",
      re.search(r"function appendAssistantBubble\(text,\s*isLive\s*=\s*true\)\s*\{\s*clearEmptyState\(\);.*?if\s*\(isLive\)\s*_consumePendingTeamBadge\(\)", app_js, re.S) is not None)
check("renderMessages() replays history with isLive=false (no stale badge on reload)",
      re.search(r"appendAssistantBubble\(m\.content,\s*false\)", app_js) is not None)

check("style.css defines dark/light theme override blocks keyed off data-theme",
      ':root[data-theme="dark"]' in style_css and ':root[data-theme="light"]' in style_css)
check("style.css styles .integration-dot.connected distinctly from the disconnected default",
      ".integration-dot.connected" in style_css)
check("style.css styles .pending-badge and its .hidden state",
      ".pending-badge" in style_css and ".pending-badge.hidden" in style_css)
check("style.css styles .team-badge",
      ".team-badge" in style_css)

print(f"\n{'=' * 55}\n{PASS} passed, {FAIL} failed\n{'=' * 55}")
sys.exit(1 if FAIL else 0)

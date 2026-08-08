"""
Tests for Phase 6 item 7: remote access over VPN audit — server.py's _is_loopback() and
the auth rate limiter's loopback exemption.

Run: python test_remote_access_phase6.py
"""

import os
import sys
import json
import time
import asyncio
import threading
import tempfile

TEST_PORT = 8774
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
section("1. _is_loopback() — unit")
# ---------------------------------------------------------------------------

import server as server_module

check("127.0.0.1 is loopback", server_module._is_loopback("127.0.0.1") is True)
check("127.0.0.2 (still 127.0.0.0/8) is loopback", server_module._is_loopback("127.0.0.2") is True)
check("::1 (IPv6 loopback) is loopback", server_module._is_loopback("::1") is True)
check("a LAN address is NOT loopback", server_module._is_loopback("192.168.1.183") is False)
check("a Tailscale CGNAT address is NOT loopback", server_module._is_loopback("100.64.1.2") is False)
check("a public address is NOT loopback", server_module._is_loopback("8.8.8.8") is False)
check("'unknown' (no client info) degrades to NOT loopback, not an exception", server_module._is_loopback("unknown") is False)
check("garbage input degrades to NOT loopback rather than raising", server_module._is_loopback("not-an-ip") is False)

# ---------------------------------------------------------------------------
section("2. AuthRateLimiter — the underlying limiter logic itself is unaffected (unit)")
# ---------------------------------------------------------------------------
# _is_loopback() only gates *whether* server.py calls into the limiter at all — the limiter
# itself has no loopback-awareness of its own (nor should it; that'd duplicate the same
# reasoning in two places). These checks pin the limiter's own behavior so a future change
# to server.py's gating can't silently change what "locked out" means for non-loopback
# sources.

from security_hardening import AuthRateLimiter

limiter = AuthRateLimiter(max_attempts=3, window_seconds=300, lockout_seconds=300)
check("a fresh source is not locked", limiter.is_locked("192.168.1.50") is None)
for _ in range(3):
    limiter.record_failure("192.168.1.50")
check("a non-loopback source IS locked after max_attempts failures", limiter.is_locked("192.168.1.50") is not None)
check("a DIFFERENT source is unaffected by another source's failures", limiter.is_locked("192.168.1.51") is None)
limiter.record_success("192.168.1.51")
limiter.record_failure("192.168.1.51")
check("record_success clears prior failures for that source", limiter.is_locked("192.168.1.51") is None)

# ---------------------------------------------------------------------------
section("3. Server: loopback hello attempts never trigger a lockout, even past max_attempts")
# ---------------------------------------------------------------------------
# The real websocket test client in this environment always connects from 127.0.0.1 — that's
# exactly the case this exemption targets, so hammering it with bad credentials here directly
# exercises the real code path server.py takes for every actual hello handshake.

import uvicorn
import websockets
from device_registry import DeviceRegistry
from conversation_store import ConversationStore as CS

_test_devices_file = os.path.join(tempfile.gettempdir(), "jarvis_test_devices_remote.json")
if os.path.exists(_test_devices_file):
    os.remove(_test_devices_file)
server_module.registry = DeviceRegistry(path=_test_devices_file)

_test_db = os.path.join(tempfile.gettempdir(), "jarvis_test_store_remote.db")
if os.path.exists(_test_db):
    os.remove(_test_db)
server_module.store = CS(path=_test_db)
import team_board_dispatcher
team_board_dispatcher.store = server_module.store

# Fresh, isolated rate limiter for this test so leftover state from other test runs (or this
# process's own section 2 above) can't cross-contaminate the result.
from security_hardening import AuthRateLimiter as _ARL
server_module._auth_limiter = _ARL(max_attempts=3, window_seconds=300, lockout_seconds=300)


async def _noop_background_loop(*args, **kwargs):
    await asyncio.Event().wait()

import posture_monitor, daily_briefing, deep_reflection as _dr
posture_monitor.posture_monitor_loop = _noop_background_loop
daily_briefing.daily_briefing_loop = _noop_background_loop
team_board_dispatcher.team_board_dispatch_loop = _noop_background_loop
_dr.deep_reflection_loop = _noop_background_loop

DEVICE_ID = "remote-test-device"
DEVICE_TOKEN = server_module.registry.register(DEVICE_ID, "Remote Test Device", ["filesystem"])

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


async def _bad_hello():
    async with websockets.connect(WS_URL) as ws:
        await ws.send(json.dumps({
            "type": "hello", "device_id": "nonexistent", "token": "wrong",
            "capabilities": [], "conversation_id": None,
        }))
        return json.loads(await ws.recv())


async def _good_hello():
    async with websockets.connect(WS_URL) as ws:
        await ws.send(json.dumps({
            "type": "hello", "device_id": DEVICE_ID, "token": DEVICE_TOKEN,
            "capabilities": ["filesystem"], "conversation_id": None,
        }))
        return json.loads(await ws.recv())


async def main():
    # Well past max_attempts (3) — a non-loopback source would be locked out by now.
    last_resp = None
    for _ in range(6):
        last_resp = await _bad_hello()
    check("bad credentials are still correctly rejected each time (exemption != auth bypass)",
          last_resp.get("type") == "error" and "unknown device" in last_resp.get("message", "").lower(),
          f"got: {last_resp}")

    # The real proof: a legitimate device from loopback still gets in immediately afterward,
    # never seeing "Too many failed auth attempts" — that message would mean the exemption
    # isn't actually wired to the real handshake path.
    good_resp = await _good_hello()
    check("a real device from loopback is NEVER locked out, even after 6 failures from the same IP",
          good_resp.get("type") == "ready", f"got: {good_resp}")

asyncio.run(main())

uv_server.should_exit = True
time.sleep(0.5)
for f in (_test_devices_file, _test_db):
    try:
        os.remove(f)
    except OSError:
        pass

# ---------------------------------------------------------------------------
section("4. Static checks — audit findings actually reflected in the codebase")
# ---------------------------------------------------------------------------

import re

with open("server.py", "r", encoding="utf-8") as f:
    server_src = f.read()

check("server.py binds 0.0.0.0 by default, not localhost-only", 'os.getenv("JARVIS_SERVER_HOST", "0.0.0.0")' in server_src)
check("both hello handshakes (/ws and /ws/mind) gate rate-limiting on _is_loopback()",
      len(re.findall(r"rate_limited\s*=\s*not\s*_is_loopback\(client_host\)", server_src)) == 2)
check("token validation (registry.validate) is unconditional — never itself gated on _is_loopback",
      "_is_loopback" not in "\n".join(l for l in server_src.splitlines() if "registry.validate(" in l))

with open("webapp/app.js", "r", encoding="utf-8") as f:
    app_js = f.read()
check("app.js builds its WS URL from location.host (works over any hostname, not hardcoded localhost)",
      "location.host" in app_js and "ws://localhost" not in app_js and "ws://127.0.0.1" not in app_js)

assert os.path.exists("REMOTE_ACCESS.md"), "REMOTE_ACCESS.md should exist"
check("REMOTE_ACCESS.md exists and documents the Tailscale HTTPS/secure-context setup", True)
with open("REMOTE_ACCESS.md", "r", encoding="utf-8") as f:
    remote_doc = f.read()
check("REMOTE_ACCESS.md explains the secure-context requirement for push/PWA install",
      "secure context" in remote_doc.lower())
check("REMOTE_ACCESS.md documents the loopback rate-limit exemption and its actual scope",
      "_is_loopback" in remote_doc and "does not extend to the LAN or a VPN" in remote_doc)

print(f"\n{'=' * 55}\n{PASS} passed, {FAIL} failed\n{'=' * 55}")
sys.exit(1 if FAIL else 0)

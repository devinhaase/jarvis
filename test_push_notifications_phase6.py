"""
Tests for Phase 6 item 6: real Web Push (VAPID + pywebpush) — push_notifications.py,
tools.py's send_test_push, and server.py's push_subscribe/push_unsubscribe/
get_push_settings/set_push_settings/test_push_notification WS handlers + /push-public-key.

Never calls the real push service: `pywebpush.webpush` is monkeypatched at the module level
(push_notifications.py imports it lazily, inside the function, specifically so a test can
patch pywebpush.webpush before that import ever executes and have it take effect).

Run: python test_push_notifications_phase6.py
"""

import os
import sys
import json
import time
import asyncio
import threading
import tempfile

TEST_PORT = 8773
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
section("1. push_notifications.py — VAPID keys, subscription CRUD, settings, quiet hours")
# ---------------------------------------------------------------------------

import push_notifications as pn

_tmpdir = tempfile.mkdtemp(prefix="jarvis_test_push_")
pn.VAPID_PRIVATE_KEY_FILE = os.path.join(_tmpdir, ".vapid_private_key.pem")
pn.SUBSCRIPTIONS_FILE = os.path.join(_tmpdir, "push_subscriptions.json")

pub1 = pn.get_vapid_public_key_b64()
check("VAPID public key is a plausible base64url P-256 uncompressed point (87 chars)", len(pub1) == 87, f"got len {len(pub1)}")
pub2 = pn.get_vapid_public_key_b64()
check("VAPID key is generated once and persisted, not regenerated each call", pub1 == pub2)
check("VAPID private key file was actually written to disk", os.path.exists(pn.VAPID_PRIVATE_KEY_FILE))

check("get_settings on a never-subscribed device reports not subscribed", pn.get_settings("nope")["subscribed"] is False)

fake_sub = {"endpoint": "https://fcm.googleapis.com/fake/abc", "keys": {"p256dh": "x", "auth": "y"}}
entry = pn.subscribe("phone", fake_sub)
check("subscribe() returns the stored entry with the subscription intact", entry["subscription"] == fake_sub)
check("subscribe() defaults categories to the documented defaults", entry["categories"] == dict(pn.DEFAULT_CATEGORIES))
check("subscribe() defaults quiet_hours to disabled", entry["quiet_hours"]["enabled"] is False)

settings = pn.get_settings("phone")
check("get_settings reports subscribed=True after subscribe()", settings["subscribed"] is True)

updated = pn.set_settings("phone", categories={"messages": True, "briefing": False})
check("set_settings updates only the given categories", updated["categories"]["messages"] is True and updated["categories"]["briefing"] is False)
check("set_settings leaves untouched categories alone", updated["categories"]["approvals"] is True)

updated2 = pn.set_settings("phone", quiet_hours={"enabled": True, "start": "23:00", "end": "06:00"})
check("set_settings updates quiet_hours", updated2["quiet_hours"] == {"enabled": True, "start": "23:00", "end": "06:00"})
check("a prior set_settings category change survives an unrelated quiet_hours-only update", updated2["categories"]["messages"] is True)

check("set_settings on a never-subscribed device is a harmless no-op", pn.set_settings("ghost", categories={"alerts": False})["subscribed"] is False)

from datetime import datetime
check("quiet hours: disabled window never applies", pn._in_quiet_hours({"enabled": False, "start": "22:00", "end": "07:00"}, datetime(2026, 1, 1, 23, 0)) is False)
check("quiet hours: wrapping window (22:00-07:00), 23:00 is inside", pn._in_quiet_hours({"enabled": True, "start": "22:00", "end": "07:00"}, datetime(2026, 1, 1, 23, 0)) is True)
check("quiet hours: wrapping window, 12:00 is outside", pn._in_quiet_hours({"enabled": True, "start": "22:00", "end": "07:00"}, datetime(2026, 1, 1, 12, 0)) is False)
check("quiet hours: non-wrapping window (09:00-17:00), 12:00 is inside", pn._in_quiet_hours({"enabled": True, "start": "09:00", "end": "17:00"}, datetime(2026, 1, 1, 12, 0)) is True)
check("quiet hours: zero-width window (start==end) never applies", pn._in_quiet_hours({"enabled": True, "start": "09:00", "end": "09:00"}, datetime(2026, 1, 1, 9, 0)) is False)
check("quiet hours: malformed times degrade to not-quiet rather than raising", pn._in_quiet_hours({"enabled": True, "start": "garbage", "end": "07:00"}) is False)

check("unsubscribe() removes the entry", pn.unsubscribe("phone") is True)
check("unsubscribe() on an already-gone device returns False, doesn't raise", pn.unsubscribe("phone") is False)
check("settings after unsubscribe reverts to not-subscribed", pn.get_settings("phone")["subscribed"] is False)

# ---------------------------------------------------------------------------
section("2. send_to_device() — gating logic, with pywebpush mocked (no real network call)")
# ---------------------------------------------------------------------------

import pywebpush as _real_pywebpush

_sent_calls = []


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class _FakeWebPushException(Exception):
    def __init__(self, status_code):
        super().__init__(f"fake {status_code}")
        self.response = _FakeResponse(status_code)


def _fake_webpush_ok(**kwargs):
    _sent_calls.append(kwargs)
    return "ok"


def _fake_webpush_gone(**kwargs):
    raise _FakeWebPushException(410)


pn.subscribe("phone2", fake_sub)

_orig_webpush = _real_pywebpush.webpush
_real_pywebpush.webpush = _fake_webpush_ok

check("send_to_device to an unsubscribed device returns False, no call made", pn.send_to_device("nobody", "alerts", "t", "b") is False)

sent = pn.send_to_device("phone2", "approvals", "Title", "Body text", tag="approval")
check("send_to_device sends when category is enabled by default (approvals)", sent is True)
check("exactly one webpush() call made", len(_sent_calls) == 1)
sent_payload = json.loads(_sent_calls[-1]["data"])
check("payload carries the title/body/category/tag", sent_payload["title"] == "Title" and sent_payload["body"] == "Body text" and sent_payload["category"] == "approvals" and sent_payload["tag"] == "approval")
check("webpush() was called with the real VAPID claims sub", _sent_calls[-1]["vapid_claims"]["sub"] == pn.VAPID_CONTACT_EMAIL)

pn.set_settings("phone2", categories={"alerts": False})
_sent_calls.clear()
sent2 = pn.send_to_device("phone2", "alerts", "t", "b")
check("send_to_device is suppressed when the category is toggled off", sent2 is False and len(_sent_calls) == 0)

sent3 = pn.send_to_device("phone2", "alerts", "t", "b", force=True)
check("force=True bypasses a disabled category (explicit test-button path)", sent3 is True)
_sent_calls.clear()

pn.set_settings("phone2", quiet_hours={"enabled": True, "start": "00:00", "end": "23:59"})
sent4 = pn.send_to_device("phone2", "approvals", "t", "b")
check("send_to_device is suppressed during quiet hours (non-critical)", sent4 is False)
sent5 = pn.send_to_device("phone2", "approvals", "t", "b", critical=True)
check("critical=True bypasses quiet hours (Tier-4 approval path)", sent5 is True)
sent6 = pn.send_to_device("phone2", "approvals", "t", "b", force=True)
check("force=True also bypasses quiet hours (test-button path)", sent6 is True)
pn.set_settings("phone2", quiet_hours={"enabled": False, "start": "22:00", "end": "07:00"})

_real_pywebpush.webpush = _fake_webpush_gone
_real_pywebpush.WebPushException = _FakeWebPushException
sent7 = pn.send_to_device("phone2", "approvals", "t", "b")
check("a 410 from the push service is treated as failure, not raised", sent7 is False)
check("a 410 auto-prunes the stale subscription", pn.get_settings("phone2")["subscribed"] is False)

_real_pywebpush.webpush = _orig_webpush

# ---------------------------------------------------------------------------
section("2b. Real, non-mocked round trip — genuine VAPID JWT + AES128GCM encryption, sent to a real local HTTP server")
# ---------------------------------------------------------------------------
# Every check above mocks pywebpush.webpush() entirely — good for testing our own gating
# logic in isolation, but it means none of them actually exercise real VAPID signing or
# real Web Push encryption. That gap is exactly how a real bug shipped during this item's
# own development and passed all the mocked tests: send_to_device() was handing
# pywebpush.webpush() a PEM *string* (decoded from Vapid02.private_pem()), but pywebpush's
# vapid_private_key parameter only accepts a Vapid instance, a *file path* string (checked
# via os.path.isfile()), or a raw base64/DER string via Vapid.from_string() — a full PEM
# string with "-----BEGIN/END-----" headers matches none of those, and from_string()
# silently mangles it into garbage that fails deep inside cryptography with an opaque
# "ASN.1 parsing error: invalid length". No mock could ever catch that; only an actual call
# into the real pywebpush/py_vapid/cryptography stack could. This section is that call —
# a real HTTP server on localhost stands in for the push service, so nothing leaves the
# machine, but every byte on the wire is genuinely VAPID-signed and AES128GCM-encrypted.

import http.server
import threading as _threading

def _real_receiver_keypair():
    from cryptography.hazmat.primitives.asymmetric import ec as _ec
    from cryptography.hazmat.primitives.serialization import Encoding as _Enc, PublicFormat as _Fmt
    from py_vapid.utils import b64urlencode as _b64e
    priv = _ec.generate_private_key(_ec.SECP256R1())
    raw = priv.public_key().public_bytes(_Enc.X962, _Fmt.UncompressedPoint)
    return _b64e(raw), _b64e(os.urandom(16))

_captured = {}

class _CatcherHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        _captured["headers"] = {k.lower(): v for k, v in self.headers.items()}
        _captured["body_len"] = len(body)
        _captured["path"] = self.path
        self.send_response(201)
        self.end_headers()

    def log_message(self, *a):
        pass


_catcher = http.server.HTTPServer(("127.0.0.1", 0), _CatcherHandler)
_catcher_port = _catcher.server_address[1]
_catcher_thread = _threading.Thread(target=_catcher.handle_request, daemon=True)
_catcher_thread.start()
time.sleep(0.3)  # give the catcher a moment to actually be listening before we send

p256dh, auth_secret = _real_receiver_keypair()
real_sub = {"endpoint": f"http://127.0.0.1:{_catcher_port}/push-endpoint", "keys": {"p256dh": p256dh, "auth": auth_secret}}
pn.subscribe("real-e2e-device", real_sub)

real_sent = pn.send_to_device("real-e2e-device", "approvals", "Real E2E", "Genuinely encrypted body.", force=True)
check("send_to_device() with the REAL (unmocked) pywebpush actually succeeds", real_sent is True)
_catcher_thread.join(timeout=3)
check("the local HTTP catcher actually received the POST", _captured.get("path") == "/push-endpoint", f"got: {_captured}")
check("the POST body is non-trivial (real AES128GCM ciphertext, not empty/placeholder)", (_captured.get("body_len") or 0) > 50, f"got: {_captured.get('body_len')}")
check("a real VAPID Authorization header (JWT) was actually sent", "vapid" in (_captured.get("headers", {}).get("authorization") or "").lower(), f"got headers: {_captured.get('headers')}")

pn.unsubscribe("real-e2e-device")

# ---------------------------------------------------------------------------
section("3. send_to_all() — broadcasts to every subscribed device")
# ---------------------------------------------------------------------------

pn.subscribe("deviceA", fake_sub)
pn.subscribe("deviceB", fake_sub)
_real_pywebpush.webpush = _fake_webpush_ok
_sent_calls.clear()
count = pn.send_to_all("briefing", "Morning briefing", "Body")
check("send_to_all reaches every subscribed device", count == 2, f"got {count}")
check("each device actually got its own webpush() call", len(_sent_calls) == 2)
_real_pywebpush.webpush = _orig_webpush
pn.unsubscribe("deviceA")
pn.unsubscribe("deviceB")
pn.unsubscribe("phone2")

# ---------------------------------------------------------------------------
section("4. tools.py — send_test_push registered, Tier 1, coordinator-level")
# ---------------------------------------------------------------------------

import tools as _tools_mod
importlib_reload_needed = "send_test_push" not in _tools_mod.ALL_TOOLS
if importlib_reload_needed:
    import importlib
    importlib.reload(_tools_mod)

check("send_test_push tool is registered", "send_test_push" in _tools_mod.ALL_TOOLS)
if "send_test_push" in _tools_mod.ALL_TOOLS:
    t = _tools_mod.ALL_TOOLS["send_test_push"]
    check("send_test_push is Tier 1", t.tier == _tools_mod.Tier.TIER_1)
    check("send_test_push is coordinator-level (team=None)", t.team is None)

# ---------------------------------------------------------------------------
section("5. Server: REST /push-public-key + WS push_subscribe/settings/test over the real protocol")
# ---------------------------------------------------------------------------

import server as server_module
import uvicorn
import websockets
from device_registry import DeviceRegistry
from conversation_store import ConversationStore as CS

_test_devices_file = os.path.join(tempfile.gettempdir(), "jarvis_test_devices_push.json")
if os.path.exists(_test_devices_file):
    os.remove(_test_devices_file)
server_module.registry = DeviceRegistry(path=_test_devices_file)

_test_db = os.path.join(tempfile.gettempdir(), "jarvis_test_store_push.db")
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

DEVICE_ID = "push-test-device"
DEVICE_TOKEN = server_module.registry.register(DEVICE_ID, "Push Test Device", ["filesystem"])

# Point the real server process's push_notifications module state at this test's isolated
# files too — server.py imports push_notifications fresh inside each handler, but Python
# module caching means it's the same module object we already patched above.
import push_notifications as _pn_check
check("server.py's lazy `import push_notifications` resolves to the same patched module", _pn_check is pn)

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

resp = urllib.request.urlopen(f"http://127.0.0.1:{TEST_PORT}/push-public-key", timeout=3)
key_payload = json.loads(resp.read())
check("/push-public-key is served unauthenticated and returns a key", len(key_payload.get("key", "")) == 87, f"got: {key_payload}")

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

        await ws.send(json.dumps({"type": "get_push_settings"}))
        resp = json.loads(await ws.recv())
        check("get_push_settings (never subscribed) returns subscribed=False", resp.get("type") == "push_settings" and resp.get("subscribed") is False, f"got: {resp}")

        await ws.send(json.dumps({"type": "push_subscribe", "subscription": fake_sub}))
        resp = json.loads(await ws.recv())
        check("push_subscribe returns subscribed=True", resp.get("subscribed") is True, f"got: {resp}")
        check("this device_id (not a client-supplied one) is what got subscribed", pn.get_settings(DEVICE_ID)["subscribed"] is True)

        await ws.send(json.dumps({"type": "set_push_settings", "categories": {"messages": True}}))
        resp = json.loads(await ws.recv())
        check("set_push_settings round-trips the updated category", resp.get("categories", {}).get("messages") is True, f"got: {resp}")

        _real_pywebpush.webpush = _fake_webpush_ok
        _sent_calls.clear()
        await ws.send(json.dumps({"type": "test_push_notification"}))
        resp = json.loads(await ws.recv())
        check("test_push_notification actually sends (force=True bypasses any gating)", resp.get("type") == "push_test_result" and resp.get("sent") is True, f"got: {resp}")
        _real_pywebpush.webpush = _orig_webpush

        await ws.send(json.dumps({"type": "push_unsubscribe"}))
        resp = json.loads(await ws.recv())
        check("push_unsubscribe returns subscribed=False", resp.get("subscribed") is False, f"got: {resp}")

asyncio.run(main())

uv_server.should_exit = True
time.sleep(0.5)
for f in (_test_devices_file, _test_db):
    try:
        os.remove(f)
    except OSError:
        pass
pn.unsubscribe(DEVICE_ID)

# ---------------------------------------------------------------------------
section("6. Static checks — client-side wiring actually present (regex-anchored)")
# ---------------------------------------------------------------------------

import re

with open("webapp/sw.js", "r", encoding="utf-8") as f:
    sw_js = f.read()
with open("webapp/app.js", "r", encoding="utf-8") as f:
    app_js = f.read()
with open("webapp/index.html", "r", encoding="utf-8") as f:
    index_html = f.read()

check("sw.js has a push event handler that calls showNotification", re.search(r'addEventListener\(\s*"push".*?showNotification', sw_js, re.S) is not None)
check("sw.js has a notificationclick handler", 'addEventListener("notificationclick"' in sw_js)
check("sw.js's notificationclick handler focuses or opens a window (deep link)", "openWindow" in sw_js and ".focus()" in sw_js)

check("app.js defines enablePushNotifications using pushManager.subscribe", re.search(r"async function enablePushNotifications.*?pushManager\.subscribe", app_js, re.S) is not None)
check("app.js sends the real subscription (not a placeholder) to the server", "push_subscribe" in app_js and "sub.toJSON()" in app_js)
check("app.js defines disablePushNotifications calling pushManager unsubscribe", re.search(r"async function disablePushNotifications.*?unsubscribe\(\)", app_js, re.S) is not None)
check("app.js handles incoming push_settings/push_test_result message types", 'case "push_settings":' in app_js and 'case "push_test_result":' in app_js)
check("app.js listens for the service worker's open_conversation postMessage (deep link)", "open_conversation" in app_js and "serviceWorker.addEventListener" in app_js)
check("app.js handles a ?conv= deep link on load", "_handleDeepLinkOnLoad" in app_js and "URLSearchParams" in app_js)

check("index.html has the notification settings button and modal", 'id="pushSettingsBtn"' in index_html and 'id="pushModal"' in index_html)
check("index.html has all 4 category toggles", all(f'id="pushCat{c}"' in index_html for c in ("Approvals", "Alerts", "Briefing", "Messages")))
check("index.html has quiet-hours controls", 'id="pushQuietEnabled"' in index_html and 'id="pushQuietStart"' in index_html and 'id="pushQuietEnd"' in index_html)

print(f"\n{'=' * 55}\n{PASS} passed, {FAIL} failed\n{'=' * 55}")
sys.exit(1 if FAIL else 0)

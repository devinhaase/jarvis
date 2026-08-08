"""
push_notifications.py — Phase 6 item 6: real Web Push (RFC 8030 + VAPID), not the plain
Notification API app.js already had. The difference matters: `new Notification(...)` (still
used by app.js's notifyIfHidden for the tab-open-but-backgrounded case) only fires while the
browser process is actually running and the page's JS is alive. A push message delivered
through the browser's push service (FCM for Chrome/Android) reaches the device's service
worker even if the browser is fully closed — the only way a phone notification can show up
"within a few seconds" of a Tier-4 approval request if you're not currently looking at the
page, which is the whole point of this item.

Design, mirroring conventions already established elsewhere in this codebase:
  - VAPID key pair generated once, persisted to data/.vapid_private_key.pem (same
    generate-once-persist-forever shape as google_auth.py's Fernet key) — never regenerated
    once it exists, since regenerating would silently invalidate every subscription the
    browser already holds (the public key is baked into the subscription itself).
  - Subscriptions keyed by device_id (device_registry.py's existing identity), not stored
    per-browser-tab — a device's most recent subscribe call replaces its old one. One
    physical device (e.g. Devin's phone, installed as a PWA) = one subscription.
  - Per-device settings (which categories, quiet hours) live alongside the subscription in
    the same file — there's nothing to configure until a device has actually subscribed.
  - Four categories, independently toggleable: "approvals" (Tier 3/4 confirmation
    requests — the main reason this item exists), "alerts" (posture_monitor findings),
    "briefing" (the daily briefing), "messages" (a completed assistant reply, mirroring
    notifyIfHidden's tab-hidden case but for when there's no tab open at all — defaults OFF
    since, unlike the other three, it fires on every single turn and would otherwise be the
    noisiest by far).
  - Quiet hours suppress everything except a Tier-4 approval (the one category where staying
    silent has a real, hard-to-undo consequence — APPROVAL_DEFAULT denies a Tier-4 action
    that times out unanswered, so silencing it during quiet hours would routinely deny things
    unattended rather than just deferring word of them). Tier-3 approvals are informational
    (APPROVAL_DEFAULT lets it proceed after just 3 seconds either way — no phone notification
    could arrive in time to change that outcome) and quiet hours suppress them like anything
    else.
  - A subscription pruned automatically on a 404/410 from the push service (the browser
    revoked it — Chrome does this on things like "clear browsing data") rather than left to
    fail forever and silently stop notifying.
"""

import os
import json
import time
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

VAPID_PRIVATE_KEY_FILE = os.path.join("data", ".vapid_private_key.pem")
SUBSCRIPTIONS_FILE = os.path.join("data", "push_subscriptions.json")
VAPID_CONTACT_EMAIL = os.getenv("JARVIS_VAPID_CONTACT_EMAIL", "mailto:devinhaase90@gmail.com")

CATEGORIES = ("approvals", "alerts", "briefing", "messages")
DEFAULT_CATEGORIES = {"approvals": True, "alerts": True, "briefing": True, "messages": False}
DEFAULT_QUIET_HOURS = {"enabled": False, "start": "22:00", "end": "07:00"}


# ---------------------------------------------------------------------------
# VAPID key management
# ---------------------------------------------------------------------------

def _vapid():
    """Returns a py_vapid.Vapid02 instance backed by the persisted key, generating one on
    first call. Cheap enough (a PEM read + parse) to not bother caching across calls — this
    only runs when actually sending a notification or serving the public key, never per
    websocket message."""
    from py_vapid import Vapid02
    os.makedirs(os.path.dirname(VAPID_PRIVATE_KEY_FILE) or ".", exist_ok=True)
    if os.path.exists(VAPID_PRIVATE_KEY_FILE):
        return Vapid02.from_file(VAPID_PRIVATE_KEY_FILE)
    vapid = Vapid02()
    vapid.generate_keys()
    vapid.save_key(VAPID_PRIVATE_KEY_FILE)
    return vapid


def get_vapid_public_key_b64() -> str:
    """The base64url (no padding), uncompressed-point public key the browser's
    `pushManager.subscribe({applicationServerKey: ...})` expects — safe to expose
    unauthenticated (it's a public key, not a secret; same trust reasoning as
    /gui-config's token — anyone on the LAN who could reach this could already reach
    everything else here)."""
    from py_vapid.utils import b64urlencode
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    v = _vapid()
    raw = v.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return b64urlencode(raw)


# ---------------------------------------------------------------------------
# Subscription + settings storage
# ---------------------------------------------------------------------------

def _load() -> dict:
    if not os.path.exists(SUBSCRIPTIONS_FILE):
        return {}
    try:
        with open(SUBSCRIPTIONS_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data: dict):
    os.makedirs(os.path.dirname(SUBSCRIPTIONS_FILE) or ".", exist_ok=True)
    with open(SUBSCRIPTIONS_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def subscribe(device_id: str, subscription_info: dict) -> dict:
    """Registers/replaces device_id's push subscription. Existing category/quiet-hours
    settings survive a re-subscribe (e.g. the browser silently rotating the endpoint) —
    only endpoint/keys are overwritten, never the human's preferences."""
    data = _load()
    existing = data.get(device_id, {})
    data[device_id] = {
        "subscription": subscription_info,
        "categories": existing.get("categories", dict(DEFAULT_CATEGORIES)),
        "quiet_hours": existing.get("quiet_hours", dict(DEFAULT_QUIET_HOURS)),
        "updated_at": time.time(),
    }
    _save(data)
    return data[device_id]


def unsubscribe(device_id: str) -> bool:
    data = _load()
    if device_id in data:
        del data[device_id]
        _save(data)
        return True
    return False


def get_settings(device_id: str) -> dict:
    entry = _load().get(device_id)
    if not entry:
        return {"subscribed": False, "categories": dict(DEFAULT_CATEGORIES), "quiet_hours": dict(DEFAULT_QUIET_HOURS)}
    return {
        "subscribed": True,
        "categories": entry.get("categories", dict(DEFAULT_CATEGORIES)),
        "quiet_hours": entry.get("quiet_hours", dict(DEFAULT_QUIET_HOURS)),
    }


def set_settings(device_id: str, categories: dict = None, quiet_hours: dict = None) -> dict:
    """Only ever adjusts prefs for a device that's already subscribed — there's nothing
    meaningful to configure otherwise. Merges rather than replaces so a client sending just
    `{"categories": {"messages": true}}` doesn't clobber quiet_hours."""
    data = _load()
    if device_id not in data:
        return get_settings(device_id)
    if categories:
        data[device_id].setdefault("categories", dict(DEFAULT_CATEGORIES))
        data[device_id]["categories"].update({k: bool(v) for k, v in categories.items() if k in CATEGORIES})
    if quiet_hours:
        data[device_id].setdefault("quiet_hours", dict(DEFAULT_QUIET_HOURS))
        data[device_id]["quiet_hours"].update(quiet_hours)
    _save(data)
    return get_settings(device_id)


# ---------------------------------------------------------------------------
# Quiet hours
# ---------------------------------------------------------------------------

def _in_quiet_hours(quiet_hours: dict, now: datetime = None) -> bool:
    """Server-local time — the one honest simplification here: this process only knows its
    own clock, not the phone's timezone, so quiet hours are "quiet hours where the server
    is," documented rather than silently assumed correct. Handles a window that wraps
    midnight (e.g. 22:00 -> 07:00)."""
    if not quiet_hours or not quiet_hours.get("enabled"):
        return False
    now = now or datetime.now()
    try:
        start_h, start_m = (int(x) for x in quiet_hours["start"].split(":"))
        end_h, end_m = (int(x) for x in quiet_hours["end"].split(":"))
    except Exception:
        return False
    start = start_h * 60 + start_m
    end = end_h * 60 + end_m
    cur = now.hour * 60 + now.minute
    if start == end:
        return False  # a zero-width window means "always off", not "always on"
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end  # wraps midnight


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

def send_to_device(device_id: str, category: str, title: str, body: str,
                    tag: str = None, conversation_id: str = None, critical: bool = False,
                    force: bool = False, dashboard: dict = None) -> bool:
    """Sends one push notification to one device, honoring its category toggle and quiet
    hours. Returns True if actually sent (not "delivered" — the push service's job, this
    codebase has no way to confirm that), False if suppressed or the device has no
    subscription. `critical=True` (used only for a Tier-4 approval, see server.py) bypasses
    quiet hours only — see the module docstring for why that specific exception exists.
    `force=True` (used only by the settings modal's explicit "send test notification"
    button) bypasses both gates: an intentional, one-off user action to verify the endpoint
    itself works shouldn't be silently swallowed by whatever category/quiet-hours prefs
    happen to be set. `dashboard` (Phase 7): optional {"team": ..., "approval_id": ...} —
    lets a tapped notification deep-link straight to a team dashboard or queue item, same
    idea as `conversation_id` but for the Overseer dashboard instead of a chat. Auto-prunes
    the subscription on 404/410 (the browser revoked it)."""
    assert category in CATEGORIES, f"unknown push category: {category!r}"
    data = _load()
    entry = data.get(device_id)
    if not entry or not entry.get("subscription"):
        return False
    if not force and not entry.get("categories", DEFAULT_CATEGORIES).get(category, True):
        return False
    if not force and not critical and _in_quiet_hours(entry.get("quiet_hours")):
        return False

    payload = {
        "title": title, "body": body[:200], "category": category,
        "tag": tag or category, "conversation_id": conversation_id, "dashboard": dashboard,
    }
    try:
        from pywebpush import webpush, WebPushException
        _vapid()  # ensures the key file exists before webpush() checks os.path.isfile() on it
        # Real bug found live-testing this item (see task.md): passing the PEM *string*
        # here (decoded from _vapid().private_pem()) reaches pywebpush's Vapid.from_string(),
        # which — despite its docstring — does NOT accept PEM text; it strips newlines and
        # tries to base64url-decode the result directly, which mangles the "-----BEGIN/END
        # PRIVATE KEY-----" header lines into garbage and fails with an opaque ASN.1 parsing
        # error. pywebpush's own webpush() checks `os.path.isfile(vapid_private_key)` first
        # and, if true, loads it correctly via Vapid.from_file() — so the fix is to hand it
        # the file *path*, not the key content, letting pywebpush's own correct code path
        # handle it instead of re-deriving the same key ourselves.
        webpush(
            subscription_info=entry["subscription"],
            data=json.dumps(payload),
            vapid_private_key=VAPID_PRIVATE_KEY_FILE,
            vapid_claims={"sub": VAPID_CONTACT_EMAIL},
            ttl=60 if critical else 1800,
        )
        return True
    except Exception as e:
        code = getattr(getattr(e, "response", None), "status_code", None)
        if code in (404, 410):
            unsubscribe(device_id)
        else:
            print(f"[push_notifications] send to {device_id!r} failed: {e}")
        return False


def send_to_all(category: str, title: str, body: str, tag: str = None,
                 conversation_id: str = None, critical: bool = False, dashboard: dict = None) -> int:
    """Broadcasts to every subscribed device (mirrors server.py's _broadcast_all — an
    approval or alert should reach any device you might have on you, not just one).
    Returns how many actually sent."""
    sent = 0
    for device_id in list(_load().keys()):
        if send_to_device(device_id, category, title, body, tag=tag,
                           conversation_id=conversation_id, critical=critical, dashboard=dashboard):
            sent += 1
    return sent

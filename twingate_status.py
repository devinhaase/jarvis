"""
twingate_status.py — Phase 8, section 5: the fail-closed gate for every firewall/NAS tool.

Twingate is a *second*, separate overlay network from Tailscale (see task.md's Phase 8 entry
for the full topology writeup) — Tailscale connects client devices (phone, etc.) to the brain
wherever it physically is; Twingate connects the brain (wherever it is) to firewall/NAS sitting
on the home LAN, via a connector Devin already has running at home. The brain itself needs the
Twingate *client* installed and signed in — confirmed NOT yet installed on this machine as of
writing this (checked `Program Files`, `sc query Twingate` — nothing there), so live output
parsing below has not yet been verified against a real running client. Written defensively
(case-insensitive substring match on the documented "online" state, collapsing anything else —
including a real client reporting "offline"/not-signed-in, and including the client simply not
being installed at all — to the same not-connected result) so it degrades safely either way;
re-verify the exact parsing once the client's actually installed and signed in here.

Modeled directly on server.py's own _tailscale_status() (same run_hardened → parse →
collapse-any-failure-to-"not connected" shape) rather than inventing a new pattern.
"""

from security_hardening import run_hardened


def twingate_status() -> dict:
    """Best-effort, read-only, never raises. Returns:
        {"detected": bool, "connected": bool}
    detected=False means "the Twingate client isn't installed/reachable at all" (binary not on
    PATH, service not running, command errored) — connected is only meaningful when
    detected=True. This mirrors _tailscale_status()'s own detected/running split.
    """
    try:
        result = run_hardened(["twingate", "status"], timeout=5)
    except Exception:
        return {"detected": False, "connected": False}

    if result.returncode != 0:
        # A real Twingate client can exit non-zero for "not signed in" as well as genuinely
        # not being installed — either way, there's nothing to route firewall/NAS calls
        # through, so both collapse to the same not-connected result rather than trying to
        # distinguish "not installed" from "installed but signed out" from process exit code
        # alone (untrustworthy without a real client here to confirm against).
        return {"detected": True, "connected": False}

    out = (result.stdout or "").strip().lower()
    return {"detected": True, "connected": "online" in out}


def is_home_network_ready() -> bool:
    """The actual gate every firewall/NAS tool calls before doing anything. True only when
    Twingate is both detected and reports online — anything else (not installed, installed but
    offline/signed-out, a parse we don't recognize) is treated as "not ready," never as "assume
    it's fine." An explicit, logged, auto-expiring override (see require_twingate_or_refuse())
    is the only way to bypass this, matching Devin's own "fail closed, not open" instruction."""
    status = twingate_status()
    return bool(status.get("connected"))


def require_twingate_or_refuse(action_description: str):
    """Call at the very top of every firewall/NAS tool, before making any request to
    OPNsense/UGOS. Returns None if the action may proceed; returns a dict (the same shape a
    tool would normally return on failure) if it must refuse — the caller does
    `blocked = require_twingate_or_refuse(...); if blocked: return blocked`.

    Scoped ONLY to home-infrastructure tools (firewall_tools.py, nas_tools.py) — nothing else
    in this codebase calls this, so Personal Assistant and every other team's tools are
    completely unaffected by Twingate being down, per Devin's explicit instruction.

    Checks the override file first (see disable_twingate_requirement below) — an active,
    unexpired override skips the live status check entirely and lets the action through, but
    every skip is a deliberate, visible choice Devin made and logged, never a silent bypass.
    """
    override = _active_override()
    if override is not None:
        return None

    if is_home_network_ready():
        return None

    return {
        "error": "refused",
        "reason": (
            f"Refusing to attempt '{action_description}' — Twingate is not connected, so "
            "there's no protected path to your home network right now. This fails closed on "
            "purpose (per your own instruction): no unprotected fallback, no silent retry. "
            "Reconnect Twingate and try again, or use disable_twingate_requirement() if you "
            "know it's flaky right now and want to proceed anyway."
        ),
    }


# ---------------------------------------------------------------------------
# The explicit, logged, Tier-3 override toggle — Devin's own escape hatch for known Twingate
# flakiness while traveling, never a silent fallback. Auto-expires so a forgotten override
# can't quietly stay off forever; state lives in data/twingate_check_override.json, the same
# "plain JSON, not secret, human-editable if needed" convention posture_monitor.py's own
# snapshot file already uses (unlike the Fernet-encrypted credential files, there's nothing
# sensitive in a timestamp and a reason string).
# ---------------------------------------------------------------------------

import json
import os
import time

_OVERRIDE_FILE = os.path.join("data", "twingate_check_override.json")


def _active_override() -> dict | None:
    """None if there's no override, or it's expired (expiry is what actually enforces
    "never a silent fallback forever" — the file being present isn't enough on its own)."""
    if not os.path.exists(_OVERRIDE_FILE):
        return None
    try:
        with open(_OVERRIDE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    if data.get("disabled_until", 0) < time.time():
        return None
    return data


def disable_twingate_requirement(reason: str, duration_minutes: int = 60) -> dict:
    """Tier 3 (see tools.py registration — every call still goes through Coordinator's
    notify-then-act window, it's not a silent no-op). Devin's own explicit, logged toggle to
    temporarily proceed with firewall/NAS actions even while Twingate looks disconnected —
    e.g. known flakiness while traveling. `reason` is required and stored, not just accepted
    and discarded, so a later `get_security_posture`/audit pass can see exactly why and when
    this was on. Capped at 24 hours per call on purpose — if it's still needed after that,
    that's worth a fresh, deliberate re-toggle, not an indefinitely-forgotten override.
    """
    duration_minutes = max(1, min(duration_minutes, 24 * 60))
    os.makedirs("data", exist_ok=True)
    payload = {
        "disabled_until": time.time() + duration_minutes * 60,
        "reason": reason,
        "set_at": time.time(),
    }
    with open(_OVERRIDE_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return {
        "disabled": True, "reason": reason, "duration_minutes": duration_minutes,
        "note": "Home-network actions will proceed without a live Twingate check until this "
                "expires. It re-enables itself automatically — no need to remember to turn it "
                "back on.",
    }


def enable_twingate_requirement() -> dict:
    """Re-enables the check immediately (Tier 3 — same notify-then-act posture as disabling
    it) rather than waiting for the natural expiry. Safe to call even if no override is
    currently active."""
    if os.path.exists(_OVERRIDE_FILE):
        os.remove(_OVERRIDE_FILE)
    return {"disabled": False, "note": "Twingate connectivity is now required again for every home-network action."}

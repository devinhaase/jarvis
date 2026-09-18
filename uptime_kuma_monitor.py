"""
uptime_kuma_monitor.py — Devin's own request: "i have an uptime kuma i want you to monitor
to make sure everything stay up." Same background-loop/snapshot/diff/post shape every other
periodic check in this project already follows (posture_monitor.py, network_monitor.py),
but on its own faster interval and its own simple state-change diff — Uptime Kuma itself
already tells us definitively up/down (no statistical baseline needed the way
network_monitor.py's traffic numbers do), so this only needs to notice when that state
*changes*, not compute anything from raw numbers.

Interval defaults short (2 minutes, not 15) — the whole point of "make sure everything
stays up" is catching an outage promptly; Uptime Kuma's own instance polls every ~20s per
Devin's real heartbeat data, so a 2-minute check-in here is still a large multiple slower
than Uptime Kuma's own polling, not a redundant hammering of it.

Flags both directions, not just "down" — a monitor recovering is worth a notification too
(so Devin doesn't have to go check it manually once he's seen the down alert), unlike
network_monitor.py's deliberately one-directional traffic checks (which only flag getting
worse, since "back to normal" isn't actionable there the same way "back up" is here).
"""

import os
import json
import asyncio

from conversation_store import store

SNAPSHOT_FILE = os.path.join("data", "uptime_kuma_snapshot.json")
MONITOR_CONV_FILE = os.path.join("data", ".uptime_kuma_monitor_conversation_id")
DEFAULT_INTERVAL_SECONDS = 2 * 60


def diff_snapshots(old_state: dict, new_monitors: list) -> tuple:
    """old_state: {monitor_name: "up"|"down"|...} from the last check. new_monitors: the
    live list from get_uptime_kuma_status(). Returns (findings: list[str], names: list[str],
    new_state: dict) — names[i] is which monitor findings[i] is about (observer.py's dedup
    key, since a monitor that flaps down/up/down/up repeatedly should be recognized as one
    recurring problem regardless of which direction it just flipped). new_state always
    reflects every monitor's current status, whether or not anything changed, so the next
    cycle has an accurate baseline regardless."""
    findings = []
    names = []
    new_state = {}

    for m in new_monitors:
        name = m["name"]
        status = m["status"]
        new_state[name] = status
        old_status = old_state.get(name)

        if old_status is None:
            continue  # first time seeing this monitor — establishes baseline, not a finding
        if old_status == status:
            continue

        if status == "down":
            findings.append(f"🔴 {name} just went DOWN (was {old_status}).")
        elif old_status == "down" and status == "up":
            findings.append(f"🟢 {name} is back UP (was down).")
        elif status in ("down", "pending") or old_status in ("down", "pending"):
            findings.append(f"{name} changed from {old_status} to {status}.")
        else:
            continue
        names.append(name)

    return findings, names, new_state


def _load_state() -> dict:
    if not os.path.exists(SNAPSHOT_FILE):
        return {}
    try:
        with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state: dict):
    os.makedirs(os.path.dirname(SNAPSHOT_FILE) or ".", exist_ok=True)
    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def _get_or_create_monitor_conversation() -> str:
    if os.path.exists(MONITOR_CONV_FILE):
        with open(MONITOR_CONV_FILE, "r") as f:
            cid = f.read().strip()
        if cid and store.conversation_exists(cid):
            return cid
    cid = store.create_conversation(device_id="uptime-kuma-monitor", title="Infrastructure Monitor")
    store.rename_conversation(cid, "Infrastructure Monitor")
    os.makedirs(os.path.dirname(MONITOR_CONV_FILE) or ".", exist_ok=True)
    with open(MONITOR_CONV_FILE, "w") as f:
        f.write(cid)
    return cid


async def _check_and_post(broadcast_all):
    import uptime_kuma_tools
    result = await asyncio.to_thread(uptime_kuma_tools.get_uptime_kuma_status)
    if "error" in result:
        return  # Uptime Kuma not reachable this cycle — not itself a finding, just skip

    old_state = _load_state()
    is_first_run = not old_state
    findings, names, new_state = diff_snapshots(old_state, result["monitors"])

    if findings and not is_first_run:
        # Category here is per-monitor-name rather than per-direction, deliberately — a
        # monitor that flaps down/up/down/up repeatedly is exactly the "already recurring,
        # stop paging for it" case observer.py exists for, and both directions belong to
        # the same underlying flapping problem.
        import observer
        novel_findings = []
        annotated_lines = []
        for finding, name in zip(findings, names):
            urgency = observer.record_and_classify("uptime_kuma", name, finding)
            if urgency == "novel":
                novel_findings.append(finding)
                annotated_lines.append(f"- {finding}")
            else:
                annotated_lines.append(f"- {finding} (recurring — auto-suppressed from push)")

        conv_id = _get_or_create_monitor_conversation()
        text = "Infrastructure monitor:\n" + "\n".join(annotated_lines)
        store.add_message(conv_id, "assistant", text, source="text")
        if broadcast_all:
            await broadcast_all({
                "type": "stream_end", "conversation_id": conv_id,
                "full_text": text, "tools_ran": [], "denied": [],
            })
            await broadcast_all({"type": "conversation_list_changed"})

        if novel_findings:
            try:
                import push_notifications as _push
                for finding in novel_findings:
                    # "down" findings page through quiet hours (critical=True) — an actual
                    # outage is exactly the kind of thing worth waking up for; a recovery
                    # notification doesn't need to.
                    is_down = "just went DOWN" in finding
                    await asyncio.to_thread(
                        _push.send_to_all, "infrastructure_down", "Jarvis: infrastructure alert",
                        finding, tag="uptime-kuma", conversation_id=conv_id, critical=is_down,
                    )
            except Exception:
                pass

    _save_state(new_state)


async def uptime_kuma_monitor_loop(broadcast_all=None, interval_seconds: int = None):
    """Runs forever until cancelled by server.py's lifespan on shutdown. First run only
    establishes the baseline state for every monitor — nothing alerts on it, same
    "don't cry wolf on a fresh install" reasoning every other monitor loop here follows.
    A cycle where Uptime Kuma itself isn't reachable just skips silently (see
    uptime_kuma_tools.get_uptime_kuma_status()'s own graceful degrade) — this is optional
    infrastructure, not something every Jarvis install has running."""
    interval = interval_seconds or int(os.getenv("JARVIS_UPTIME_KUMA_INTERVAL_SECONDS", str(DEFAULT_INTERVAL_SECONDS)))
    while True:
        try:
            await _check_and_post(broadcast_all)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[UptimeKumaMonitor] check failed: {e}")
        await asyncio.sleep(interval)

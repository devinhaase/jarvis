"""
network_monitor.py — Phase 8, section 3: periodic network baseline snapshots from the
firewall/NAS, diffed against a rolling baseline (not fixed thresholds — per Devin's own
instruction: "build a baseline of normal traffic... and flag deviations, rather than
trying to hand-define 'bad traffic' as a fixed rule list"), with findings posted into a
dedicated "Network Monitor" conversation. Modeled directly on posture_monitor.py's own
loop — same background-asyncio-task-in-server.py's-lifespan shape, same snapshot-diff-post
pattern, same graceful-degrade-on-failure posture — see that file for the original
reasoning this one reuses rather than reinvents.

Every check below reads through firewall_tools.py/nas_tools.py, which means every read
already goes through the same Twingate fail-closed gate the tools themselves enforce
(twingate_status.require_twingate_or_refuse()). This loop treats a refused/unconfigured
read exactly like posture_monitor.py treats a get_security_posture() failure on a
non-Windows host: skip this field for this cycle, never crash the loop, never treat "no
data available" as "nothing's wrong."

What it flags (all Tier 1 — informational only, per Devin's explicit instruction: "flagging
a threat is Tier 1... anything the agent does in response... is Tier 4 unless I've
explicitly pre-approved a specific automated response"):
  - A device MAC never seen before (checked against a cumulative "ever seen" set, not just
    the immediately-prior snapshot — a laptop that was merely off for one check interval
    shouldn't look "new" the next time it reconnects).
  - Failed-login count meaningfully above its own rolling average.
  - Bandwidth or active-connection count meaningfully outside its own rolling range.
  - Any NAS backup task reporting a failed status.
"""

import os
import json
import time
import asyncio
import statistics

from conversation_store import store

SNAPSHOT_FILE = os.path.join("data", "network_baseline_snapshot.json")
MONITOR_CONV_FILE = os.path.join("data", ".network_monitor_conversation_id")
DEFAULT_INTERVAL_SECONDS = 15 * 60

# How many past samples to keep for the rolling baseline — enough to smooth out normal
# variation without taking forever to establish a meaningful baseline after a fresh start.
HISTORY_LENGTH = 20
# A sample counts as "anomalous" when it's this many standard deviations from the rolling
# mean — a genuine statistical-deviation check, not a hand-picked absolute number (e.g.
# "flag anything over 500Mbps"), per Devin's explicit instruction. Needs at least a handful
# of samples before this means anything; see _is_anomalous()'s own minimum-history guard.
ANOMALY_STDEV_THRESHOLD = 3.0
MIN_HISTORY_FOR_ANOMALY_CHECK = 5


def _is_anomalous(history: list, new_value) -> bool:
    """True if new_value is a genuine statistical outlier against history's own rolling
    mean/stdev — not against any fixed number chosen in advance. Requires at least
    MIN_HISTORY_FOR_ANOMALY_CHECK prior samples (a baseline of 1-2 points can't tell normal
    variation from a real anomaly) and a non-zero stdev (a perfectly flat history — e.g.
    every prior sample identical — would otherwise divide by zero or flag any change at
    all, neither of which is the intent)."""
    if new_value is None or len(history) < MIN_HISTORY_FOR_ANOMALY_CHECK:
        return False
    mean = statistics.mean(history)
    stdev = statistics.pstdev(history)
    if stdev == 0:
        return False
    return abs(new_value - mean) > ANOMALY_STDEV_THRESHOLD * stdev


def _safe_call(fn, *args, **kwargs):
    """Every firewall_tools/nas_tools function already returns a plain dict rather than
    raising — including on a Twingate refusal or missing credentials. This just recognizes
    that error shape and turns it into None ("no data this cycle"), the one normalization
    point every caller below shares rather than repeating an isinstance-and-"error"-key
    check inline everywhere."""
    try:
        result = fn(*args, **kwargs)
    except Exception:
        return None
    if isinstance(result, dict) and "error" in result:
        return None
    return result


def _extract_device_macs(devices_result) -> set:
    """The firewall's ARP table shape isn't confirmed against a live OPNsense instance yet
    (see firewall_tools.py's own honesty note) — written defensively to pull a MAC-shaped
    field out of whatever list-of-dicts structure comes back rather than assuming one exact
    key name, so a close-but-not-identical real response still yields something useful
    instead of silently extracting nothing."""
    macs = set()
    devices = (devices_result or {}).get("devices")
    if not isinstance(devices, list):
        return macs
    for entry in devices:
        if not isinstance(entry, dict):
            continue
        for key in ("mac", "MAC", "mac_addr", "macaddr", "lladdr"):
            if entry.get(key):
                macs.add(str(entry[key]).lower())
                break
    return macs


def _count_failed_logins(logs_result) -> int:
    """Counts log entries whose text plainly indicates a failed authentication attempt.
    Same defensive posture as _extract_device_macs — the firewall log entry shape isn't
    confirmed live yet, so this looks for the substance (failure-indicating text) rather
    than an exact schema."""
    logs = (logs_result or {}).get("logs")
    if not isinstance(logs, list):
        return 0
    count = 0
    for entry in logs:
        text = json.dumps(entry).lower() if isinstance(entry, (dict, list)) else str(entry).lower()
        if any(kw in text for kw in ("auth fail", "login fail", "invalid credential", "denied")):
            count += 1
    return count


def _sum_bandwidth(bandwidth_result) -> float:
    interfaces = (bandwidth_result or {}).get("interfaces")
    if not isinstance(interfaces, dict):
        return None
    total = 0
    found = False
    for stats in interfaces.values():
        if isinstance(stats, dict):
            for key in ("bytes_in", "bytes_out", "in_bytes", "out_bytes"):
                if key in stats and isinstance(stats[key], (int, float)):
                    total += stats[key]
                    found = True
    return total if found else None


def _count_connections(connections_result) -> int:
    conns = (connections_result or {}).get("connections")
    if isinstance(conns, list):
        return len(conns)
    return None


def _failed_backup_names(backup_result) -> list:
    tasks = (backup_result or {}).get("tasks")
    if not isinstance(tasks, dict):
        return []
    entries = tasks.get("data") if isinstance(tasks.get("data"), list) else tasks.get("list")
    if not isinstance(entries, list):
        return []
    failed = []
    for t in entries:
        if isinstance(t, dict) and str(t.get("status", "")).lower() in ("failed", "error"):
            failed.append(t.get("name") or t.get("task_name") or "(unnamed task)")
    return failed


def _take_snapshot() -> dict:
    """One read of every source this loop watches — every individual read degrades to None
    independently (a firewall outage doesn't also blank out NAS backup status, and vice
    versa), matching posture_monitor.py's per-field tolerance."""
    import firewall_tools
    import nas_tools

    devices = _safe_call(firewall_tools.get_connected_devices)
    logs = _safe_call(firewall_tools.get_firewall_logs)
    bandwidth = _safe_call(firewall_tools.get_bandwidth_usage)
    connections = _safe_call(firewall_tools.get_active_connections)
    backups = _safe_call(nas_tools.get_nas_backup_status)

    return {
        "captured_at": time.time(),
        "device_macs": sorted(_extract_device_macs(devices)) if devices is not None else None,
        "failed_login_count": _count_failed_logins(logs) if logs is not None else None,
        "bandwidth_total": _sum_bandwidth(bandwidth) if bandwidth is not None else None,
        "connection_count": _count_connections(connections) if connections is not None else None,
        "failed_backups": _failed_backup_names(backups) if backups is not None else None,
    }


def diff_snapshots(baseline_state: dict, new_snapshot: dict) -> tuple:
    """Compares new_snapshot against the running baseline_state (NOT just the prior single
    snapshot — see module docstring for why "new device" needs a cumulative history, and
    the anomaly checks need a rolling window, not a single prior data point).

    Returns (findings: list[str], updated_baseline_state: dict) — the caller always saves
    updated_baseline_state, whether or not there were findings, so the rolling history
    keeps moving forward every cycle.
    """
    findings = []

    all_seen_macs = set(baseline_state.get("all_seen_macs", []))
    if new_snapshot["device_macs"] is not None:
        new_macs = set(new_snapshot["device_macs"]) - all_seen_macs
        if new_macs:
            findings.append(f"New/unknown device(s) joined the network: {', '.join(sorted(new_macs))}.")
        all_seen_macs |= set(new_snapshot["device_macs"])

    failed_login_history = list(baseline_state.get("failed_login_history", []))
    if new_snapshot["failed_login_count"] is not None:
        if _is_anomalous(failed_login_history, new_snapshot["failed_login_count"]):
            mean = statistics.mean(failed_login_history)
            findings.append(
                f"Repeated failed logins: {new_snapshot['failed_login_count']} this check, "
                f"well above the recent average (~{mean:.1f})."
            )
        failed_login_history = (failed_login_history + [new_snapshot["failed_login_count"]])[-HISTORY_LENGTH:]

    bandwidth_history = list(baseline_state.get("bandwidth_history", []))
    if new_snapshot["bandwidth_total"] is not None:
        if _is_anomalous(bandwidth_history, new_snapshot["bandwidth_total"]):
            findings.append("Anomalous traffic volume: bandwidth is well outside its recent normal range.")
        bandwidth_history = (bandwidth_history + [new_snapshot["bandwidth_total"]])[-HISTORY_LENGTH:]

    connection_history = list(baseline_state.get("connection_history", []))
    if new_snapshot["connection_count"] is not None:
        if _is_anomalous(connection_history, new_snapshot["connection_count"]):
            findings.append("Anomalous traffic: active connection count is well outside its recent normal range.")
        connection_history = (connection_history + [new_snapshot["connection_count"]])[-HISTORY_LENGTH:]

    if new_snapshot["failed_backups"]:
        findings.append(f"Backup failure(s): {', '.join(new_snapshot['failed_backups'])}.")

    updated_state = {
        "all_seen_macs": sorted(all_seen_macs),
        "failed_login_history": failed_login_history,
        "bandwidth_history": bandwidth_history,
        "connection_history": connection_history,
        "last_captured_at": new_snapshot["captured_at"],
    }
    return findings, updated_state


def _load_baseline_state() -> dict:
    if not os.path.exists(SNAPSHOT_FILE):
        return {}
    try:
        with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_baseline_state(state: dict):
    os.makedirs(os.path.dirname(SNAPSHOT_FILE) or ".", exist_ok=True)
    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def _get_or_create_monitor_conversation() -> str:
    if os.path.exists(MONITOR_CONV_FILE):
        with open(MONITOR_CONV_FILE, "r") as f:
            cid = f.read().strip()
        if cid and store.conversation_exists(cid):
            return cid
    cid = store.create_conversation(device_id="network-monitor", title="Network Monitor")
    store.rename_conversation(cid, "Network Monitor")
    os.makedirs(os.path.dirname(MONITOR_CONV_FILE) or ".", exist_ok=True)
    with open(MONITOR_CONV_FILE, "w") as f:
        f.write(cid)
    return cid


# category -> which push_notifications.py category this finding maps to, so one loop can
# feed four different notification toggles rather than lumping everything under "alerts"
# the way posture_monitor.py does (Devin's spec explicitly asks for these as distinct
# categories a device can enable/disable independently).
_FINDING_CATEGORY = {
    "New/unknown device": "new_device",
    "Repeated failed logins": "failed_logins",
    "Anomalous traffic": "network_anomaly",
    "Anomalous traffic volume": "network_anomaly",
    "Backup failure": "backup_failure",
}


def _category_for(finding: str) -> str:
    for prefix, category in _FINDING_CATEGORY.items():
        if finding.startswith(prefix):
            return category
    return "network_anomaly"  # shouldn't happen, but never let an unmatched finding go unsent


async def _take_snapshot_and_check(broadcast_all):
    new_snapshot = await asyncio.to_thread(_take_snapshot)
    baseline_state = _load_baseline_state()
    is_first_run = not baseline_state

    findings, updated_state = diff_snapshots(baseline_state, new_snapshot)

    if findings and not is_first_run:
        conv_id = _get_or_create_monitor_conversation()
        text = "Network monitor:\n" + "\n".join(f"- {f}" for f in findings)
        store.add_message(conv_id, "assistant", text, source="text")
        if broadcast_all:
            await broadcast_all({
                "type": "stream_end", "conversation_id": conv_id,
                "full_text": text, "tools_ran": [], "denied": [],
            })
            await broadcast_all({"type": "conversation_list_changed"})

        try:
            import push_notifications as _push
            for finding in findings:
                await asyncio.to_thread(
                    _push.send_to_all, _category_for(finding), "Jarvis: network alert",
                    finding, tag="network-monitor", conversation_id=conv_id,
                )
        except Exception:
            pass

    _save_baseline_state(updated_state)


async def network_monitor_loop(broadcast_all=None, interval_seconds: int = None):
    """Runs forever until cancelled by server.py's lifespan on shutdown. The first run only
    establishes the baseline (all_seen_macs/history start empty) — nothing alerts on it,
    same "don't cry wolf on a fresh install" reasoning posture_monitor.py already applies.
    A cycle where Twingate is down (routine now that the brain travels — see task.md's
    Phase 8 entry) just produces None for every field; diff_snapshots treats a None field
    as "nothing to compare," never as an anomaly in itself."""
    interval = interval_seconds or int(os.getenv("JARVIS_NETWORK_MONITOR_INTERVAL_SECONDS", str(DEFAULT_INTERVAL_SECONDS)))
    while True:
        try:
            await _take_snapshot_and_check(broadcast_all)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[NetworkMonitor] check failed: {e}")
        await asyncio.sleep(interval)

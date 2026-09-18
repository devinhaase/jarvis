"""
posture_monitor.py — Phase 3e "Defense": periodic security posture snapshots, diffed
against the last one, with findings posted into a dedicated "Security Monitor" conversation
— drift shows up the same way any other Jarvis message does, no separate notification
system, no new UI to build or miss.

Runs as a background asyncio task inside server.py's lifespan (see _lifespan in server.py).
If security_tools.get_security_posture isn't importable (non-Windows, or the security
tools failed to load), server.py just doesn't start this loop — same silent-degrade
convention as the rest of security tool registration in tools.py.

What it checks (parsed out of get_security_posture()'s raw PowerShell-JSON strings, which
is why _parse_snapshot exists — the tool returns text meant for an LLM to read, not
already-structured data to diff):
  - Windows Defender: antivirus / real-time protection / antispyware toggled OFF
  - Any firewall profile toggled OFF
  - Pending Windows updates count going up
  - Any new listening TCP port that wasn't there last check
"""

import os
import json
import time
import asyncio

from conversation_store import store

SNAPSHOT_FILE = os.path.join("data", "posture_snapshot.json")
MONITOR_CONV_FILE = os.path.join("data", ".security_monitor_conversation_id")
DEFAULT_INTERVAL_SECONDS = 15 * 60


def _as_list(parsed):
    """PowerShell's ConvertTo-Json returns a bare object (not a 1-element array) when
    there's exactly one result — Get-MpComputerStatus always does this; Get-NetTCPConnection
    /Get-NetFirewallProfile do it too whenever there's only one row. Normalize both shapes."""
    if parsed is None:
        return []
    return parsed if isinstance(parsed, list) else [parsed]


def _safe_json(raw):
    try:
        return json.loads(raw)
    except Exception:
        return None


def _parse_snapshot(posture: dict) -> dict:
    """Turn get_security_posture()'s raw, stringly-typed result into a normalized,
    actually-diffable structure. Any field that fails to parse becomes None/empty rather
    than raising — a monitor that crashes because one PowerShell call had odd output on one
    run defeats the point of running unattended."""
    parsed = {"captured_at": time.time()}

    defender = _safe_json(posture.get("windows_defender", ""))
    parsed["defender"] = (
        {k: defender.get(k) for k in ("AntivirusEnabled", "RealTimeProtectionEnabled", "AntispywareEnabled")}
        if isinstance(defender, dict) else None
    )

    firewall = _safe_json(posture.get("firewall", ""))
    parsed["firewall"] = (
        {row.get("Name"): row.get("Enabled") for row in _as_list(firewall) if isinstance(row, dict)}
        if firewall is not None else None
    )

    pending_raw = (posture.get("pending_updates") or "").strip()
    first_token = pending_raw.split()[0] if pending_raw else ""
    parsed["pending_updates"] = int(first_token) if first_token.isdigit() else None

    listening = _safe_json(posture.get("listening_services", ""))
    ports = set()
    for row in _as_list(listening):
        if isinstance(row, dict) and row.get("LocalPort") is not None:
            ports.add(f"{row.get('LocalAddress', '?')}:{row.get('LocalPort')}")
    parsed["listening_ports"] = sorted(ports)

    return parsed


def _category_for(finding: str) -> str:
    """Mirrors network_monitor.py's own _category_for() — the dedup key observer.py needs
    to tell "this same kind of posture change keeps happening" from a genuinely new one."""
    if finding.startswith(("Antivirus", "Real-time protection", "Antispyware")):
        return "defender"
    if finding.startswith("Firewall profile"):
        return "firewall"
    if finding.startswith("Pending Windows updates"):
        return "updates"
    if finding.startswith("New listening port"):
        return "new_port"
    return "posture_other"


def diff_snapshots(old: dict, new: dict) -> list:
    """Returns a list of human-readable finding strings — empty if nothing notable changed.
    Deliberately one-directional on Defender/firewall (flags OFF, not back ON) and on
    updates (flags increases, not decreases) — those directions are the ones worth waking
    someone up for."""
    findings = []

    if old.get("defender") and new.get("defender"):
        for key, label in (
            ("AntivirusEnabled", "Antivirus"),
            ("RealTimeProtectionEnabled", "Real-time protection"),
            ("AntispywareEnabled", "Antispyware"),
        ):
            if old["defender"].get(key) is True and new["defender"].get(key) is False:
                findings.append(f"{label} was ON, is now OFF.")

    if old.get("firewall") and new.get("firewall"):
        for profile, was_on in old["firewall"].items():
            if was_on is True and new["firewall"].get(profile) is False:
                findings.append(f"Firewall profile '{profile}' was ON, is now OFF.")

    if isinstance(old.get("pending_updates"), int) and isinstance(new.get("pending_updates"), int):
        if new["pending_updates"] > old["pending_updates"]:
            findings.append(f"Pending Windows updates increased: {old['pending_updates']} -> {new['pending_updates']}.")

    newly_opened = set(new.get("listening_ports") or []) - set(old.get("listening_ports") or [])
    if newly_opened:
        findings.append(f"New listening port(s) since last check: {', '.join(sorted(newly_opened))}.")

    return findings


def _load_snapshot():
    if not os.path.exists(SNAPSHOT_FILE):
        return None
    try:
        with open(SNAPSHOT_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return None


def _save_snapshot(snapshot: dict):
    os.makedirs(os.path.dirname(SNAPSHOT_FILE) or ".", exist_ok=True)
    with open(SNAPSHOT_FILE, 'w') as f:
        json.dump(snapshot, f, indent=2)


def _get_or_create_monitor_conversation() -> str:
    if os.path.exists(MONITOR_CONV_FILE):
        with open(MONITOR_CONV_FILE, 'r') as f:
            cid = f.read().strip()
        if cid and store.conversation_exists(cid):
            return cid
    cid = store.create_conversation(device_id="posture-monitor", title="Security Monitor")
    store.rename_conversation(cid, "Security Monitor")
    os.makedirs(os.path.dirname(MONITOR_CONV_FILE) or ".", exist_ok=True)
    with open(MONITOR_CONV_FILE, 'w') as f:
        f.write(cid)
    return cid


async def _take_snapshot_and_check(broadcast_all):
    from security_tools import get_security_posture
    posture = await asyncio.to_thread(get_security_posture)
    new_snapshot = _parse_snapshot(posture)
    old_snapshot = _load_snapshot()

    if old_snapshot is not None:
        findings = diff_snapshots(old_snapshot, new_snapshot)
        if findings:
            # See observer.py's own docstring — the same "3+ times in a week stops being
            # news" classification network_monitor.py uses, applied here too rather than
            # paging for e.g. a firewall profile that some other app keeps toggling.
            import observer
            novel_findings = []
            annotated_lines = []
            for finding in findings:
                urgency = observer.record_and_classify("posture", _category_for(finding), finding)
                if urgency == "novel":
                    novel_findings.append(finding)
                    annotated_lines.append(f"- {finding}")
                else:
                    annotated_lines.append(f"- {finding} (recurring — auto-suppressed from push)")

            conv_id = _get_or_create_monitor_conversation()
            text = "Posture change detected:\n" + "\n".join(annotated_lines)
            store.add_message(conv_id, "assistant", text, source="text")
            if broadcast_all:
                # Renders live for anyone with this conversation open right now...
                await broadcast_all({
                    "type": "stream_end", "conversation_id": conv_id,
                    "full_text": text, "tools_ran": [], "denied": [],
                })
                # ...and makes every connected device's sidebar notice the conversation
                # even if they weren't watching it, so a fresh finding doesn't sit
                # invisible until someone happens to reopen "Security Monitor".
                await broadcast_all({"type": "conversation_list_changed"})
            # Phase 6 item 6: "alerts" category — a posture regression is exactly the kind
            # of thing worth reaching a phone for, not just a conversation nobody has open.
            if novel_findings:
                try:
                    import push_notifications as _push
                    headline = novel_findings[0] if len(novel_findings) == 1 else f"{len(novel_findings)} changes — {novel_findings[0]}"
                    await asyncio.to_thread(_push.send_to_all, "alerts", "Jarvis: security posture change", headline,
                                             tag="posture", conversation_id=conv_id)
                except Exception:
                    pass

    _save_snapshot(new_snapshot)


async def posture_monitor_loop(broadcast_all=None, interval_seconds: int = None):
    """Runs forever (until cancelled by server.py's lifespan on shutdown). Takes a posture
    snapshot every `interval_seconds` (default 15 minutes, or JARVIS_POSTURE_INTERVAL_SECONDS
    from .env), diffs it against the last one, posts findings into the Security Monitor
    conversation. The first run after a fresh install only establishes a baseline — there's
    nothing to diff against yet, so nothing alerts on it."""
    interval = interval_seconds or int(os.getenv("JARVIS_POSTURE_INTERVAL_SECONDS", str(DEFAULT_INTERVAL_SECONDS)))
    while True:
        try:
            await _take_snapshot_and_check(broadcast_all)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[PostureMonitor] check failed: {e}")
        await asyncio.sleep(interval)

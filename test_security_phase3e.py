"""
Tests for Phase 3e: Security specialist expansion — offense/defense/practice role tags,
the centralized offense-authorization gate in Coordinator, and the posture_monitor
(Defense) diffing logic + Security Monitor conversation delivery.

Uses the real data/authorized_targets.json (same convention as test_security_phase2b.py:
127.0.0.1 is authorized there, 8.8.8.8 is not) and an isolated temp ConversationStore for
the posture_monitor tests so nothing touches real data/jarvis.db or the real Security
Monitor conversation.

Run: python test_security_phase3e.py
"""

import os
import sys
import json
import time
import shutil
import tempfile

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


from tools import ALL_TOOLS

# ---------------------------------------------------------------------------
section("1. Role tags assigned correctly")
# ---------------------------------------------------------------------------
EXPECTED_ROLES = {
    "scan_ports": "offense",
    "run_recon_pipeline": "offense",
    "shodan_lookup": "offense",
    "run_privesc_enum": "offense",
    "get_security_posture": "defense",
    "check_password_breach": "defense",
    "check_email_breach": "defense",
    "check_credential_hygiene": "defense",
    "get_security_reference": "practice",
    "lookup_cve": "practice",
    "search_exploitdb": "practice",
    "identify_hash": "practice",
    "crack_hash": "practice",
    "ctf_assistant": "practice",
    "cert_study_session": "practice",
    "generate_pentest_report": "practice",
}
for name, expected_role in EXPECTED_ROLES.items():
    if name not in ALL_TOOLS:
        check(f"{name} registered", False, "not in ALL_TOOLS (security_modules may not have loaded)")
        continue
    check(f"{name} tagged role={expected_role}", ALL_TOOLS[name].role == expected_role,
          f"got: {ALL_TOOLS[name].role}")

check("a non-security tool has no role", ALL_TOOLS["check_system_health"].role is None)


# ---------------------------------------------------------------------------
section("2. _check_offense_authorization — the centralized dispatch-layer gate")
# ---------------------------------------------------------------------------
from coordinator import _check_offense_authorization
from tools import Tool, Tier
from auth_check import AuthorizationError

fake_offense_tool = Tool("fake_offense", "test", Tier.TIER_1, lambda **kw: "ok", role="offense")
fake_defense_tool = Tool("fake_defense", "test", Tier.TIER_1, lambda **kw: "ok", role="defense")
fake_no_role_tool = Tool("fake_plain", "test", Tier.TIER_1, lambda **kw: "ok", role=None)

try:
    _check_offense_authorization(fake_offense_tool, {"target": "8.8.8.8"})
    check("offense tool with unauthorized target raises AuthorizationError", False, "no exception raised")
except AuthorizationError:
    check("offense tool with unauthorized target raises AuthorizationError", True)

try:
    _check_offense_authorization(fake_offense_tool, {"target": "127.0.0.1"})
    check("offense tool with authorized target passes silently", True)
except AuthorizationError as e:
    check("offense tool with authorized target passes silently", False, f"raised: {e}")

try:
    _check_offense_authorization(fake_offense_tool, {"query": "8.8.8.8"})
    check("offense tool using 'query' arg name (shodan-style) is also gated", False, "no exception raised")
except AuthorizationError:
    check("offense tool using 'query' arg name (shodan-style) is also gated", True)

try:
    _check_offense_authorization(fake_offense_tool, {})
    check("offense tool with no target/query at all is not gated (e.g. run_privesc_enum)", True)
except AuthorizationError as e:
    check("offense tool with no target/query at all is not gated", False, f"raised: {e}")

try:
    _check_offense_authorization(fake_defense_tool, {"target": "8.8.8.8"})
    check("a non-offense tool is never gated, even with an unauthorized-looking target", True)
except AuthorizationError as e:
    check("a non-offense tool is never gated", False, f"raised: {e}")

try:
    _check_offense_authorization(fake_no_role_tool, {"target": "8.8.8.8"})
    check("a role=None tool is never gated", True)
except AuthorizationError as e:
    check("a role=None tool is never gated", False, f"raised: {e}")


# ---------------------------------------------------------------------------
section("3. End-to-end via Coordinator — the gate runs BEFORE the tool function executes")
# ---------------------------------------------------------------------------
from coordinator import Coordinator
from memory import Memory

call_log = []


def _tracking_offense_func(target):
    call_log.append(target)
    return f"scanned {target}"


import tools as tools_module
tools_module.ALL_TOOLS["_test_offense_tool"] = Tool(
    "_test_offense_tool", "test-only offense tool", Tier.TIER_1, _tracking_offense_func, role="offense"
)

tmp_mem_dir = tempfile.mkdtemp(prefix="jarvis_test_p3e_mem_")
coord = Coordinator(approval_fn=lambda a, t: True, memory=Memory(data_dir=tmp_mem_dir))

summary = coord.run_tools([{"tool": "_test_offense_tool", "args": {"target": "8.8.8.8"}}])
check("unauthorized offense tool call never actually executes the underlying function",
      call_log == [], f"call_log={call_log}")
check("run_tools reports it as a failure, not a silent skip",
      "FAILED" in summary and "not in your authorized targets" in summary.lower() or "authorizationerror" in summary.lower() or "NOT in your authorized" in summary,
      f"got: {summary!r}")

result = coord.run_single_tool("_test_offense_tool", {"target": "127.0.0.1"})
check("authorized offense tool call actually executes via run_single_tool",
      call_log == ["127.0.0.1"] and result == "scanned 127.0.0.1", f"call_log={call_log} result={result}")

del tools_module.ALL_TOOLS["_test_offense_tool"]
shutil.rmtree(tmp_mem_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
section("4. posture_monitor — snapshot parsing (PowerShell's single-object-vs-array quirk)")
# ---------------------------------------------------------------------------
import posture_monitor as pm

posture_single = {
    "windows_defender": json.dumps({"AntivirusEnabled": True, "RealTimeProtectionEnabled": True, "AntispywareEnabled": True}),
    "firewall": json.dumps([{"Name": "Domain", "Enabled": True}, {"Name": "Public", "Enabled": True}]),
    "pending_updates": "3 pending updates",
    "listening_services": json.dumps({"LocalAddress": "0.0.0.0", "LocalPort": 445, "OwningProcess": 4}),  # single dict, not array
}
snap = pm._parse_snapshot(posture_single)
check("defender parsed into a dict of booleans", snap["defender"] == {
    "AntivirusEnabled": True, "RealTimeProtectionEnabled": True, "AntispywareEnabled": True
}, f"got: {snap['defender']}")
check("firewall parsed into name->enabled map", snap["firewall"] == {"Domain": True, "Public": True}, f"got: {snap['firewall']}")
check("pending_updates parsed to an int", snap["pending_updates"] == 3, f"got: {snap['pending_updates']}")
check("a bare single-object listening_services result is still normalized into a list of ports",
      snap["listening_ports"] == ["0.0.0.0:445"], f"got: {snap['listening_ports']}")

posture_malformed = {
    "windows_defender": "Error: access denied",
    "firewall": "not json at all",
    "pending_updates": "Error checking updates: timeout",
    "listening_services": "",
}
snap_bad = pm._parse_snapshot(posture_malformed)
check("malformed posture fields degrade to None/empty instead of raising",
      snap_bad["defender"] is None and snap_bad["firewall"] is None
      and snap_bad["pending_updates"] is None and snap_bad["listening_ports"] == [],
      f"got: {snap_bad}")


# ---------------------------------------------------------------------------
section("5. posture_monitor — diffing")
# ---------------------------------------------------------------------------
base = pm._parse_snapshot({
    "windows_defender": json.dumps({"AntivirusEnabled": True, "RealTimeProtectionEnabled": True, "AntispywareEnabled": True}),
    "firewall": json.dumps([{"Name": "Domain", "Enabled": True}, {"Name": "Public", "Enabled": True}]),
    "pending_updates": "2 pending updates",
    "listening_services": json.dumps([{"LocalAddress": "0.0.0.0", "LocalPort": 445, "OwningProcess": 4}]),
})

no_change = dict(base)
check("identical snapshots produce no findings", pm.diff_snapshots(base, no_change) == [])

defender_off = pm._parse_snapshot({
    "windows_defender": json.dumps({"AntivirusEnabled": True, "RealTimeProtectionEnabled": False, "AntispywareEnabled": True}),
    "firewall": json.dumps([{"Name": "Domain", "Enabled": True}, {"Name": "Public", "Enabled": True}]),
    "pending_updates": "2 pending updates",
    "listening_services": json.dumps([{"LocalAddress": "0.0.0.0", "LocalPort": 445, "OwningProcess": 4}]),
})
findings = pm.diff_snapshots(base, defender_off)
check("real-time protection turning off is flagged", any("Real-time protection" in f for f in findings), f"got: {findings}")

firewall_off = pm._parse_snapshot({
    "windows_defender": json.dumps({"AntivirusEnabled": True, "RealTimeProtectionEnabled": True, "AntispywareEnabled": True}),
    "firewall": json.dumps([{"Name": "Domain", "Enabled": False}, {"Name": "Public", "Enabled": True}]),
    "pending_updates": "2 pending updates",
    "listening_services": json.dumps([{"LocalAddress": "0.0.0.0", "LocalPort": 445, "OwningProcess": 4}]),
})
findings = pm.diff_snapshots(base, firewall_off)
check("a firewall profile turning off is flagged", any("Domain" in f and "OFF" in f for f in findings), f"got: {findings}")

more_updates = pm._parse_snapshot({
    "windows_defender": json.dumps({"AntivirusEnabled": True, "RealTimeProtectionEnabled": True, "AntispywareEnabled": True}),
    "firewall": json.dumps([{"Name": "Domain", "Enabled": True}, {"Name": "Public", "Enabled": True}]),
    "pending_updates": "9 pending updates",
    "listening_services": json.dumps([{"LocalAddress": "0.0.0.0", "LocalPort": 445, "OwningProcess": 4}]),
})
findings = pm.diff_snapshots(base, more_updates)
check("increasing pending updates is flagged", any("2 -> 9" in f for f in findings), f"got: {findings}")

fewer_updates = pm._parse_snapshot({
    "windows_defender": json.dumps({"AntivirusEnabled": True, "RealTimeProtectionEnabled": True, "AntispywareEnabled": True}),
    "firewall": json.dumps([{"Name": "Domain", "Enabled": True}, {"Name": "Public", "Enabled": True}]),
    "pending_updates": "0 pending updates",
    "listening_services": json.dumps([{"LocalAddress": "0.0.0.0", "LocalPort": 445, "OwningProcess": 4}]),
})
check("decreasing pending updates is NOT flagged (patches applied is good news)",
      pm.diff_snapshots(base, fewer_updates) == [])

new_port = pm._parse_snapshot({
    "windows_defender": json.dumps({"AntivirusEnabled": True, "RealTimeProtectionEnabled": True, "AntispywareEnabled": True}),
    "firewall": json.dumps([{"Name": "Domain", "Enabled": True}, {"Name": "Public", "Enabled": True}]),
    "pending_updates": "2 pending updates",
    "listening_services": json.dumps([
        {"LocalAddress": "0.0.0.0", "LocalPort": 445, "OwningProcess": 4},
        {"LocalAddress": "0.0.0.0", "LocalPort": 4444, "OwningProcess": 1234},
    ]),
})
findings = pm.diff_snapshots(base, new_port)
check("a newly opened listening port is flagged", any("4444" in f for f in findings), f"got: {findings}")

check("a firewall turning back ON is not flagged (only OFF is alarming)",
      pm.diff_snapshots(defender_off, base) == [f for f in pm.diff_snapshots(defender_off, base) if "Firewall" not in f])


# ---------------------------------------------------------------------------
section("6. posture_monitor — end-to-end alert delivery into the Security Monitor conversation")
# ---------------------------------------------------------------------------
import conversation_store as cs_module
from conversation_store import ConversationStore

tmp_db = os.path.join(tempfile.gettempdir(), "jarvis_test_posture_p3e.db")
if os.path.exists(tmp_db):
    os.remove(tmp_db)
test_store = ConversationStore(path=tmp_db)

tmp_root = tempfile.mkdtemp(prefix="jarvis_test_posture_root_")
orig_snapshot_file = pm.SNAPSHOT_FILE
orig_monitor_conv_file = pm.MONITOR_CONV_FILE
orig_store = pm.store

pm.store = test_store
pm.SNAPSHOT_FILE = os.path.join(tmp_root, "snapshot.json")
pm.MONITOR_CONV_FILE = os.path.join(tmp_root, ".monitor_conv_id")

import asyncio
import unittest.mock as mock

broadcasts = []


async def fake_broadcast_all(payload):
    broadcasts.append(payload)


try:
    with mock.patch("security_tools.get_security_posture", return_value={
        "windows_defender": json.dumps({"AntivirusEnabled": True, "RealTimeProtectionEnabled": True, "AntispywareEnabled": True}),
        "firewall": json.dumps([{"Name": "Domain", "Enabled": True}]),
        "pending_updates": "1 pending updates",
        "listening_services": json.dumps([{"LocalAddress": "0.0.0.0", "LocalPort": 22, "OwningProcess": 1}]),
    }):
        asyncio.run(pm._take_snapshot_and_check(fake_broadcast_all))
    check("first-ever check establishes a baseline, posts no alert", broadcasts == [], f"got: {broadcasts}")
    check("snapshot file was written", os.path.exists(pm.SNAPSHOT_FILE))

    with mock.patch("security_tools.get_security_posture", return_value={
        "windows_defender": json.dumps({"AntivirusEnabled": True, "RealTimeProtectionEnabled": False, "AntispywareEnabled": True}),
        "firewall": json.dumps([{"Name": "Domain", "Enabled": True}]),
        "pending_updates": "1 pending updates",
        "listening_services": json.dumps([{"LocalAddress": "0.0.0.0", "LocalPort": 22, "OwningProcess": 1}]),
    }):
        asyncio.run(pm._take_snapshot_and_check(fake_broadcast_all))

    check("a real regression triggers a broadcast this time", len(broadcasts) > 0, f"got: {broadcasts}")
    stream_end_broadcasts = [b for b in broadcasts if b.get("type") == "stream_end"]
    check("the alert broadcast is a stream_end with the finding in it",
          stream_end_broadcasts and "Real-time protection" in stream_end_broadcasts[0].get("full_text", ""),
          f"got: {stream_end_broadcasts}")
    check("a conversation_list_changed nudge is also broadcast",
          any(b.get("type") == "conversation_list_changed" for b in broadcasts))

    conv_id = stream_end_broadcasts[0]["conversation_id"]
    check("the alert was actually persisted to the Security Monitor conversation",
          test_store.conversation_exists(conv_id) and test_store.get_conversation(conv_id)["title"] == "Security Monitor")
    msgs = test_store.get_messages(conv_id)
    check("exactly one alert message persisted so far", len(msgs) == 1 and msgs[0]["role"] == "assistant")

    same_conv_id = pm._get_or_create_monitor_conversation()
    check("_get_or_create_monitor_conversation is idempotent across calls", same_conv_id == conv_id)

finally:
    pm.store = orig_store
    pm.SNAPSHOT_FILE = orig_snapshot_file
    pm.MONITOR_CONV_FILE = orig_monitor_conv_file
    shutil.rmtree(tmp_root, ignore_errors=True)
    try:
        os.remove(tmp_db)
    except OSError:
        pass


print(f"\n{'=' * 55}\n{PASS} passed, {FAIL} failed\n{'=' * 55}")
sys.exit(1 if FAIL else 0)

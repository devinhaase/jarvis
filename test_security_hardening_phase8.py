"""
Tests for Phase 8: security hardening — subprocess env-stripping (run_hardened), log/output
redaction (redact), the kill switch (arm/disarm/status + dispatch-layer enforcement in
coordinator.py), device-auth rate limiting (AuthRateLimiter), and the self-audit tool.

Run: python test_security_hardening_phase8.py
"""

import os
import sys
import time
import json
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


import security_hardening as sh

# ---------------------------------------------------------------------------
section("run_hardened() — env stripping")
# ---------------------------------------------------------------------------

os.environ["JARVIS_TEST_SECRET_XYZ"] = "supersecretvalue123"
try:
    result = sh.run_hardened(["cmd", "/c", "echo %JARVIS_TEST_SECRET_XYZ%"], timeout=15)
    out = (result.stdout or "") + (result.stderr or "")
    check("child process does NOT see a var outside the safe-env allowlist",
          "supersecretvalue123" not in out, f"got: {out!r}")
finally:
    del os.environ["JARVIS_TEST_SECRET_XYZ"]

result = sh.run_hardened(["cmd", "/c", "echo hello-hardened"], timeout=15)
check("child process still runs and produces real output", "hello-hardened" in (result.stdout or ""), f"got: {result.stdout!r}")

env = sh._minimal_env()
check("minimal env includes PATH (child needs it to find executables)", "PATH" in env)
check("minimal env excludes arbitrary app secrets", "OPENAI_API_KEY" not in env or not os.environ.get("OPENAI_API_KEY"))

try:
    # `timeout /t` errors out immediately when stdin isn't an interactive console (as it
    # isn't under subprocess) rather than actually sleeping — ping's wait between packets
    # doesn't have that dependency, so it reliably exercises the timeout path instead.
    sh.run_hardened(["ping", "-n", "6", "127.0.0.1"], timeout=1)
    check("timeout enforcement", False, "expected TimeoutExpired, none raised")
except Exception as e:
    check("timeout enforcement raises TimeoutExpired", "Timeout" in type(e).__name__, f"got: {type(e).__name__}")

# ---------------------------------------------------------------------------
section("redact()")
# ---------------------------------------------------------------------------

check("OpenAI-shaped key redacted", "[REDACTED:OPENAI_KEY]" in sh.redact("key is sk-" + "a" * 30))
check("AWS access key id redacted", "[REDACTED:AWS_KEY_ID]" in sh.redact("AKIA1234567890ABCDEF found"))
check("private key block redacted", "[REDACTED:PRIVATE_KEY]" in sh.redact(
    "-----BEGIN PRIVATE KEY-----\nMIIBogIBAA==\n-----END PRIVATE KEY-----"))
check("key=value shape redacted", "[REDACTED]" in sh.redact("api_key=abcd1234efgh5678"))
check("password=value shape redacted", "[REDACTED]" in sh.redact("password: hunter2hunter2"))
check("long hex token redacted", "[REDACTED:HEX_TOKEN]" in sh.redact("a" * 48))
check("ordinary text passes through unchanged", sh.redact("the system is healthy, cpu at 12%") == "the system is healthy, cpu at 12%")
check("empty/None input doesn't raise", sh.redact("") == "" and sh.redact(None) is None)

# ---------------------------------------------------------------------------
section("Kill switch")
# ---------------------------------------------------------------------------

# Isolate from any real kill-switch state a live server might have set.
_real_file = sh._KILL_SWITCH_FILE
_tmp_dir = tempfile.mkdtemp()
sh._KILL_SWITCH_FILE = os.path.join(_tmp_dir, "KILL_SWITCH")

try:
    check("not armed by default", sh.is_kill_switch_armed() is False)
    check("status reports not armed", sh.kill_switch_status() == {"armed": False})

    msg = sh.arm_kill_switch("testing")
    check("arm returns a confirmation message", "ARMED" in msg)
    check("is_kill_switch_armed() now True", sh.is_kill_switch_armed() is True)
    status = sh.kill_switch_status()
    check("status reports armed with reason", status["armed"] is True and status["reason"] == "testing", f"got {status}")

    msg2 = sh.disarm_kill_switch()
    check("disarm returns confirmation", "disarmed" in msg2.lower())
    check("is_kill_switch_armed() now False", sh.is_kill_switch_armed() is False)

    msg3 = sh.disarm_kill_switch()
    check("disarming when not armed is a no-op, not an error", "was not armed" in msg3.lower())
finally:
    sh._KILL_SWITCH_FILE = _real_file
    shutil.rmtree(_tmp_dir, ignore_errors=True)

# ---------------------------------------------------------------------------
section("Kill switch enforcement at dispatch (coordinator.py)")
# ---------------------------------------------------------------------------

import coordinator as coord

_real_file2 = sh._KILL_SWITCH_FILE
_tmp_dir2 = tempfile.mkdtemp()
sh._KILL_SWITCH_FILE = os.path.join(_tmp_dir2, "KILL_SWITCH")
# coordinator.py did `from security_hardening import is_kill_switch_armed, kill_switch_status`
# — those names are bound to the SAME function objects as sh.is_kill_switch_armed etc., which
# read sh._KILL_SWITCH_FILE as a global at call time, so patching it on sh above is enough;
# no need to touch anything on the coordinator module itself.
try:
    from tools import Tool, Tier

    tier1_tool = Tool("t1", "read-only", Tier.TIER_1, lambda: "ok")
    tier2_tool = Tool("t2", "writes something", Tier.TIER_2, lambda: "ok")
    exempt_tool = Tool("disarm_kill_switch", "must always work", Tier.TIER_2, lambda: "ok")

    check("Tier 1 tool passes through even when unarmed (baseline)", coord._check_kill_switch(tier1_tool) is None)

    sh.arm_kill_switch("dispatch test")
    try:
        coord._check_kill_switch(tier1_tool)
        check("Tier 1 tools still run while armed (read-only shouldn't be blocked)", True)
    except sh.KillSwitchActive:
        check("Tier 1 tools still run while armed", False, "Tier 1 was blocked")

    try:
        coord._check_kill_switch(tier2_tool)
        check("Tier 2+ tool is refused while armed", False, "no exception raised")
    except sh.KillSwitchActive as e:
        check("Tier 2+ tool is refused while armed", "ARMED" in str(e), f"got: {e}")

    try:
        coord._check_kill_switch(exempt_tool)
        check("disarm_kill_switch is exempt from its own block (else arming is one-way)", True)
    except sh.KillSwitchActive:
        check("disarm_kill_switch is exempt from its own block", False, "was blocked")

    sh.disarm_kill_switch()
    check("Tier 2+ tool runs again after disarm", coord._check_kill_switch(tier2_tool) is None)
finally:
    sh._KILL_SWITCH_FILE = _real_file2
    shutil.rmtree(_tmp_dir2, ignore_errors=True)

# ---------------------------------------------------------------------------
section("run_tools() end-to-end honors the kill switch")
# ---------------------------------------------------------------------------

_real_file3 = sh._KILL_SWITCH_FILE
_tmp_dir3 = tempfile.mkdtemp()
sh._KILL_SWITCH_FILE = os.path.join(_tmp_dir3, "KILL_SWITCH")
try:
    sh.arm_kill_switch("end to end")
    c = coord.Coordinator(approval_fn=lambda name, tier: True)
    summary = c.run_tools([{"tool": "create_backup", "args": {}}])
    check("run_tools halts on an armed kill switch instead of executing", "Kill switch is ARMED" in summary, f"got: {summary}")
finally:
    sh.disarm_kill_switch()
    sh._KILL_SWITCH_FILE = _real_file3
    shutil.rmtree(_tmp_dir3, ignore_errors=True)

# ---------------------------------------------------------------------------
section("AuthRateLimiter")
# ---------------------------------------------------------------------------

limiter = sh.AuthRateLimiter(max_attempts=3, window_seconds=60, lockout_seconds=60)
check("not locked initially", limiter.is_locked("1.2.3.4") is None)

for _ in range(2):
    limiter.record_failure("1.2.3.4")
check("not locked after 2/3 failures", limiter.is_locked("1.2.3.4") is None)

limiter.record_failure("1.2.3.4")
check("locked after reaching max_attempts", limiter.is_locked("1.2.3.4") is not None)

check("a different source is unaffected", limiter.is_locked("5.6.7.8") is None)

limiter2 = sh.AuthRateLimiter(max_attempts=3, window_seconds=60, lockout_seconds=60)
limiter2.record_failure("9.9.9.9")
limiter2.record_failure("9.9.9.9")
limiter2.record_success("9.9.9.9")
for _ in range(2):
    limiter2.record_failure("9.9.9.9")
check("record_success resets the failure count", limiter2.is_locked("9.9.9.9") is None)

# ---------------------------------------------------------------------------
section("run_self_audit()")
# ---------------------------------------------------------------------------

audit = sh.run_self_audit()
check("returns kill_switch/findings/ok/status keys", all(k in audit for k in ("kill_switch", "findings", "ok", "status")), f"got: {audit.keys()}")
check("status is CLEAN or ATTENTION", audit["status"] in ("CLEAN", "ATTENTION"), f"got: {audit['status']}")
check("no sensitive files reported as tracked in git in this real repo",
      not any("SENSITIVE FILES ARE TRACKED" in f for f in audit["findings"]), f"findings: {audit['findings']}")

# ---------------------------------------------------------------------------
section("Tools registered in ALL_TOOLS")
# ---------------------------------------------------------------------------

from tools import ALL_TOOLS, Tier as T

for name in ("arm_kill_switch", "disarm_kill_switch", "kill_switch_status", "run_self_audit"):
    check(f"'{name}' registered in ALL_TOOLS", name in ALL_TOOLS, f"available: {sorted(ALL_TOOLS.keys())}")

check("arm_kill_switch is Tier 1 (must never be blocked)", ALL_TOOLS["arm_kill_switch"].tier == T.TIER_1)
check("disarm_kill_switch is Tier 2", ALL_TOOLS["disarm_kill_switch"].tier == T.TIER_2)


print(f"\n{'=' * 55}\n{PASS} passed, {FAIL} failed\n{'=' * 55}")
sys.exit(1 if FAIL else 0)

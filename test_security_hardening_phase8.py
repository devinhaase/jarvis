"""
Tests for Phase 8: security hardening — subprocess env-stripping (run_hardened), log/output
redaction (redact), device-auth rate limiting (AuthRateLimiter), and the self-audit tool.

Run: python test_security_hardening_phase8.py
"""

import os
import sys
import time
import json

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

import coordinator as coord

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
check("returns findings/ok/status keys", all(k in audit for k in ("findings", "ok", "status")), f"got: {audit.keys()}")
check("status is CLEAN or ATTENTION", audit["status"] in ("CLEAN", "ATTENTION"), f"got: {audit['status']}")
check("no sensitive files reported as tracked in git in this real repo",
      not any("SENSITIVE FILES ARE TRACKED" in f for f in audit["findings"]), f"findings: {audit['findings']}")

# ---------------------------------------------------------------------------
section("Tools registered in ALL_TOOLS")
# ---------------------------------------------------------------------------

from tools import ALL_TOOLS, Tier as T

check("'run_self_audit' registered in ALL_TOOLS", "run_self_audit" in ALL_TOOLS, f"available: {sorted(ALL_TOOLS.keys())}")
check("run_self_audit is Tier 1 (read-only)", ALL_TOOLS["run_self_audit"].tier == T.TIER_1)


print(f"\n{'=' * 55}\n{PASS} passed, {FAIL} failed\n{'=' * 55}")
sys.exit(1 if FAIL else 0)

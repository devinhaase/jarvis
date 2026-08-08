"""
Tests for Phase 2b-finish:
- recon_tools.py / ctf_tools.py wired into ALL_TOOLS
- shodan_lookup() gated by AuthorizationCheck
- crack_hash() requires a valid `source` tag
- render_posture_dashboard() renders without crashing on real or malformed input

Run: python test_security_phase2b.py
No network/nmap/hashcat required — tests are written to pass on a bare install.
"""

import sys

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
section("1. Tool registration")
# ---------------------------------------------------------------------------
from tools import ALL_TOOLS

expected_new_tools = [
    "lookup_cve", "search_exploitdb", "run_recon_pipeline",
    "shodan_lookup", "run_privesc_enum",
    "identify_hash", "crack_hash", "ctf_assistant",
    "cert_study_session", "generate_pentest_report",
]
for name in expected_new_tools:
    check(f"'{name}' registered in ALL_TOOLS", name in ALL_TOOLS, f"missing: {name}")

if "crack_hash" in ALL_TOOLS:
    check(
        "crack_hash description documents the source requirement",
        "source" in ALL_TOOLS["crack_hash"].description.lower(),
    )


# ---------------------------------------------------------------------------
section("2. shodan_lookup authorization gate")
# ---------------------------------------------------------------------------
from security_modules.recon_tools import shodan_lookup
from auth_check import AuthorizationError

try:
    shodan_lookup("8.8.8.8")  # not in authorized_targets.json
    check("shodan_lookup blocks unauthorized target", False, "no exception raised")
except AuthorizationError:
    check("shodan_lookup blocks unauthorized target", True)
except Exception as e:
    check("shodan_lookup blocks unauthorized target", False, f"wrong exception type: {type(e).__name__}: {e}")

try:
    result = shodan_lookup("127.0.0.1")  # authorized in authorized_targets.json
    check(
        "shodan_lookup allows authorized target (passes auth, then fails on missing API key, doesn't raise)",
        isinstance(result, dict) and "error" in result,
        f"got: {result}"
    )
except AuthorizationError:
    check("shodan_lookup allows authorized target", False, "AuthorizationError raised for an authorized target")


# ---------------------------------------------------------------------------
section("3. crack_hash source gate")
# ---------------------------------------------------------------------------
from security_modules.ctf_tools import crack_hash, ScopeError

try:
    crack_hash("5f4dcc3b5aa765d61d8327deb882cf99")  # missing required `source`
    check("crack_hash refuses call with no source arg", False, "no exception raised")
except TypeError:
    check("crack_hash refuses call with no source arg", True, "(TypeError — missing required positional arg)")
except Exception as e:
    check("crack_hash refuses call with no source arg", False, f"wrong exception: {type(e).__name__}: {e}")

try:
    crack_hash("5f4dcc3b5aa765d61d8327deb882cf99", source="live_target")
    check("crack_hash rejects an invalid source tag", False, "no exception raised")
except ScopeError:
    check("crack_hash rejects an invalid source tag", True)
except Exception as e:
    check("crack_hash rejects an invalid source tag", False, f"wrong exception: {type(e).__name__}: {e}")

try:
    result = crack_hash("5f4dcc3b5aa765d61d8327deb882cf99", source="practice")
    check(
        "crack_hash proceeds normally with a valid source tag",
        isinstance(result, dict) and ("cracked" in result or "error" in result),
        f"got: {result}"
    )
except ScopeError:
    check("crack_hash proceeds normally with a valid source tag", False, "ScopeError raised for a valid source")


# ---------------------------------------------------------------------------
section("4. Posture dashboard rendering")
# ---------------------------------------------------------------------------
from security_tools import render_posture_dashboard

well_formed = {
    "windows_defender": '{"AntivirusEnabled":true,"RealTimeProtectionEnabled":true,"AntispywareEnabled":true}',
    "firewall": '[{"Name":"Domain","Enabled":true},{"Name":"Public","Enabled":false}]',
    "pending_updates": "3 pending updates",
    "listening_services": '[{"LocalAddress":"0.0.0.0","LocalPort":445,"OwningProcess":4}]',
}
malformed = {
    "windows_defender": "Error: access denied",
    "firewall": "",
    "pending_updates": "Error checking updates: timeout",
    "listening_services": "not json at all",
}
empty = {}

for label, posture in (("well-formed data", well_formed), ("malformed data", malformed), ("empty dict", empty)):
    try:
        panel = render_posture_dashboard(posture)
        check(f"renders without raising ({label})", panel is not None)
    except Exception as e:
        check(f"renders without raising ({label})", False, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
print(f"\n{'=' * 40}\n{PASS} passed, {FAIL} failed\n{'=' * 40}")
sys.exit(1 if FAIL else 0)

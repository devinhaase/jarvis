"""
Tests for Phase 5: expanded Security specialist — OSINT/recon tools (osint_tools.py),
static reference tools (reference_tools.py), and a dependency vulnerability auditor
(dependency_audit.py).

Authorization gating is tested against the REAL data/authorized_targets.json (127.0.0.1/
localhost are already authorized there, same convention as test_security_phase2b.py and
test_security_phase3e.py). Live internet-dependent checks (crt.sh, TLS, HTTP headers) use
example.com — the domain IANA/RFC 2606 explicitly reserves for exactly this kind of use —
via a temporarily monkeypatched auth_check.AUTHORIZED_TARGETS_FILE pointed at an isolated
temp file, so the real authorized_targets.json is never touched.

Run: python test_security_expansion_phase5.py
"""

import os
import sys
import json
import tempfile
import unittest.mock as mock

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
from auth_check import AuthorizationError

# ---------------------------------------------------------------------------
section("1. Tool registration and role tags")
# ---------------------------------------------------------------------------
EXPECTED = {
    "dns_recon": "offense", "subdomain_enum": "offense", "whois_lookup": "offense",
    "tech_fingerprint": "offense", "check_ssl_cert": "offense", "http_security_headers_audit": "offense",
    "mitre_attack_lookup": "practice", "owasp_top10_reference": "practice",
    "check_dependency_vulnerabilities": "defense",
}
for name, role in EXPECTED.items():
    if name not in ALL_TOOLS:
        check(f"{name} registered", False, "not in ALL_TOOLS — did security_modules import cleanly?")
        continue
    check(f"{name} tagged role={role}", ALL_TOOLS[name].role == role, f"got: {ALL_TOOLS[name].role}")

from tools import REQUIRES_CAPABILITY
for name in ("dns_recon", "whois_lookup", "check_ssl_cert", "check_dependency_vulnerabilities"):
    check(f"{name} requires 'filesystem' capability", REQUIRES_CAPABILITY.get(name) == "filesystem")
for name in ("subdomain_enum", "tech_fingerprint", "http_security_headers_audit",
             "mitre_attack_lookup", "owasp_top10_reference"):
    check(f"{name} has no capability requirement (pure network/static, no local touch)",
          name not in REQUIRES_CAPABILITY)


# ---------------------------------------------------------------------------
section("2. Authorization gating — unauthorized targets refused (real authorized_targets.json)")
# ---------------------------------------------------------------------------
from security_modules.osint_tools import (
    dns_recon, subdomain_enum, whois_lookup, tech_fingerprint,
    check_ssl_cert, http_security_headers_audit,
)

UNAUTHORIZED = "definitely-not-authorized-example-9f8e7d.com"

for label, fn, kwargs in (
    ("dns_recon", dns_recon, {"domain": UNAUTHORIZED}),
    ("subdomain_enum", subdomain_enum, {"domain": UNAUTHORIZED}),
    ("whois_lookup", whois_lookup, {"target": UNAUTHORIZED}),
    ("tech_fingerprint", tech_fingerprint, {"url": f"https://{UNAUTHORIZED}"}),
    ("check_ssl_cert", check_ssl_cert, {"host": UNAUTHORIZED}),
    ("http_security_headers_audit", http_security_headers_audit, {"url": f"https://{UNAUTHORIZED}"}),
):
    try:
        fn(**kwargs)
        check(f"{label} refuses an unauthorized target", False, "no exception raised")
    except AuthorizationError:
        check(f"{label} refuses an unauthorized target", True)
    except Exception as e:
        check(f"{label} refuses an unauthorized target", False, f"wrong exception: {type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
section("3. Centralized dispatch-layer gate now recognizes 'domain'/'host' too")
# ---------------------------------------------------------------------------
from coordinator import _check_offense_authorization
from tools import Tool, Tier

fake_domain_tool = Tool("fake_domain_tool", "test", Tier.TIER_1, lambda **kw: "ok", role="offense")
try:
    _check_offense_authorization(fake_domain_tool, {"domain": UNAUTHORIZED})
    check("centralized gate catches an offense tool's 'domain' arg", False, "no exception raised")
except AuthorizationError:
    check("centralized gate catches an offense tool's 'domain' arg", True)

try:
    _check_offense_authorization(fake_domain_tool, {"host": UNAUTHORIZED})
    check("centralized gate catches an offense tool's 'host' arg", False, "no exception raised")
except AuthorizationError:
    check("centralized gate catches an offense tool's 'host' arg", True)

try:
    _check_offense_authorization(fake_domain_tool, {"url": f"https://{UNAUTHORIZED}/path"})
    check("centralized gate deliberately does NOT gate a bare 'url' arg (would false-reject legit targets)", True)
except AuthorizationError as e:
    check("centralized gate deliberately does NOT gate a bare 'url' arg", False,
          f"raised (should have been silently skipped, left to the tool's own check): {e}")


# ---------------------------------------------------------------------------
section("4. Authorized-target paths actually run (using real authorized_targets.json entries)")
# ---------------------------------------------------------------------------
try:
    result = dns_recon("localhost")
    check("dns_recon on an authorized target doesn't raise AuthorizationError",
          "records" in result, f"got: {result}")
except AuthorizationError as e:
    check("dns_recon on an authorized target doesn't raise AuthorizationError", False, f"raised: {e}")

try:
    result = check_ssl_cert("127.0.0.1", port=443)
    # Almost certainly no TLS listener on 127.0.0.1:443 on a dev machine — that's fine,
    # the assertion is only that it got PAST the auth gate, not that the connection succeeds.
    check("check_ssl_cert on an authorized target doesn't raise AuthorizationError",
          isinstance(result, dict), f"got: {result}")
except AuthorizationError as e:
    check("check_ssl_cert on an authorized target doesn't raise AuthorizationError", False, f"raised: {e}")


# ---------------------------------------------------------------------------
section("5. Live checks against example.com (RFC 2606 reserved test domain) — best-effort")
# ---------------------------------------------------------------------------
_tmp_authorized = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
json.dump({"hosts": ["example.com"], "domains": ["example.com"], "networks": []}, _tmp_authorized)
_tmp_authorized.close()

try:
    import requests as _r
    _r.get("https://example.com", timeout=5)
    internet_up = True
except Exception:
    internet_up = False

if not internet_up:
    print("  [SKIP] No internet reachability to example.com — skipping live checks.")
else:
    with mock.patch("auth_check.AUTHORIZED_TARGETS_FILE", _tmp_authorized.name):
        try:
            result = subdomain_enum("example.com")
            check("subdomain_enum against example.com returns a result without raising",
                  "subdomains" in result, f"got: {result}")
        except Exception as e:
            check("subdomain_enum against example.com returns a result without raising", False, f"raised: {e}")

        try:
            result = tech_fingerprint("https://example.com")
            check("tech_fingerprint against example.com gets a real HTTP response",
                  result.get("status_code") == 200, f"got: {result}")
        except Exception as e:
            check("tech_fingerprint against example.com gets a real HTTP response", False, f"raised: {e}")

        try:
            result = check_ssl_cert("example.com")
            check("check_ssl_cert against example.com returns real certificate data",
                  result.get("issuer") is not None and isinstance(result.get("days_until_expiry"), int),
                  f"got: {result}")
        except Exception as e:
            check("check_ssl_cert against example.com returns real certificate data", False, f"raised: {e}")

        try:
            result = http_security_headers_audit("https://example.com")
            check("http_security_headers_audit against example.com returns a real scorecard",
                  "score" in result and result.get("status_code") == 200, f"got: {result}")
        except Exception as e:
            check("http_security_headers_audit against example.com returns a real scorecard", False, f"raised: {e}")

os.unlink(_tmp_authorized.name)


# ---------------------------------------------------------------------------
section("6. mitre_attack_lookup")
# ---------------------------------------------------------------------------
from security_modules.reference_tools import mitre_attack_lookup, owasp_top10_reference

result = mitre_attack_lookup("T1110")
check("exact technique ID lookup finds Brute Force",
      result["count"] == 1 and result["matches"][0]["technique_name"] == "Brute Force", f"got: {result}")

result = mitre_attack_lookup("phishing")
check("keyword search finds Phishing under initial-access",
      any(m["technique_name"] == "Phishing" for m in result["matches"]), f"got: {result}")

result = mitre_attack_lookup("persistence")
check("tactic-name search returns multiple techniques under that tactic",
      result["count"] >= 2 and all(m["tactic"] == "persistence" for m in result["matches"]), f"got: {result}")

result = mitre_attack_lookup("totally-not-a-real-technique-xyz")
check("no match returns an empty list plus available tactics, not an error",
      result["matches"] == [] and "available_tactics" in result, f"got: {result}")

result = mitre_attack_lookup("")
check("empty query handled gracefully", result["matches"] == [])


# ---------------------------------------------------------------------------
section("7. owasp_top10_reference")
# ---------------------------------------------------------------------------
result = owasp_top10_reference()
check("no topic returns all 10 categories", len(result["categories"]) == 10, f"got: {len(result.get('categories', []))}")

result = owasp_top10_reference("A03")
check("lookup by code", result.get("name") == "Injection", f"got: {result}")

result = owasp_top10_reference("access control")
check("lookup by name keyword", result.get("code") == "A01", f"got: {result}")

result = owasp_top10_reference("not a real category")
check("no match returns available list, not an error", result.get("match") is None and "available" in result, f"got: {result}")


# ---------------------------------------------------------------------------
section("8. check_dependency_vulnerabilities")
# ---------------------------------------------------------------------------
from security_modules.dependency_audit import check_dependency_vulnerabilities, _parse_requirements

tmp_req = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
tmp_req.write(
    "# a comment\n"
    "requests==2.25.0\n"          # older, exactly-pinned — good parse case
    "some-package>=1.0,<2.0\n"    # range — should be skipped, not parsed as pinned
    "-e git+https://example.com/repo.git#egg=thing\n"  # editable install — skipped
    "\n"
    "unpinned-package\n"          # no version at all — skipped
)
tmp_req.close()

pinned, skipped, err = _parse_requirements(tmp_req.name)
check("parser extracts exactly the one properly-pinned entry", pinned == [("requests", "2.25.0")], f"got: {pinned}")
check("parser reports the range/-e/unpinned lines as skipped, not silently dropped", len(skipped) == 3, f"got: {skipped}")

result = check_dependency_vulnerabilities(requirements_path="/definitely/not/a/real/path.txt")
check("missing requirements file returns a clear error, doesn't raise", "error" in result, f"got: {result}")

# Regression check: an all-unpinned file (e.g. this project's own requirements.txt, which
# lists bare package names with no version pins) must use the SAME "skipped" key name in
# this early-return branch as the full-result branch below does — a real bug where the
# no-pinned-entries path used a different key ("skipped" vs "skipped_unpinned_or_unparsed")
# was caught by actually running this against Jarvis's own requirements.txt.
all_unpinned = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
all_unpinned.write("some-package\nanother-package\n")
all_unpinned.close()
result = check_dependency_vulnerabilities(requirements_path=all_unpinned.name)
check("all-unpinned file reports 0 checked with a clear note",
      result.get("checked") == 0 and "note" in result, f"got: {result}")
check("all-unpinned file uses the SAME skipped-entries key as the full-result path",
      result.get("skipped_unpinned_or_unparsed") == ["some-package", "another-package"], f"got: {result}")
os.unlink(all_unpinned.name)

if internet_up:
    result = check_dependency_vulnerabilities(requirements_path=tmp_req.name)
    check("live OSV.dev check runs against a real pinned package without raising",
          "checked" in result and result["checked"] == 1, f"got: {result}")
    check("skipped entries are still reported alongside the real check",
          len(result.get("skipped_unpinned_or_unparsed", [])) == 3, f"got: {result}")
else:
    print("  [SKIP] No internet reachability — skipping live OSV.dev check.")

# Running it against this project's OWN requirements.txt should just work, no crash —
# the whole point of defaulting to it.
if os.path.exists("requirements.txt") and internet_up:
    result = check_dependency_vulnerabilities()
    check("default call against this project's own requirements.txt doesn't raise",
          "error" not in result or "checked" in result, f"got: {result}")

os.unlink(tmp_req.name)


print(f"\n{'=' * 55}\n{PASS} passed, {FAIL} failed\n{'=' * 55}")
sys.exit(1 if FAIL else 0)

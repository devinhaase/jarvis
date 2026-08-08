"""
Tests for Phase 7: web_search.py — general-purpose web search tool.

Run: python test_web_search_phase7.py
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


from tools import ALL_TOOLS, Tier

# ---------------------------------------------------------------------------
section("1. Tool registration")
# ---------------------------------------------------------------------------
check("web_search registered", "web_search" in ALL_TOOLS)
if "web_search" in ALL_TOOLS:
    check("web_search is Tier 1 (read-only)", ALL_TOOLS["web_search"].tier == Tier.TIER_1)
    check("web_search has no security role tag (general Assistant tool, not security-scoped)",
          ALL_TOOLS["web_search"].role is None)
from tools import REQUIRES_CAPABILITY
check("web_search has no capability requirement (pure outbound network call)",
      "web_search" not in REQUIRES_CAPABILITY)

# ---------------------------------------------------------------------------
section("2. Empty query handled gracefully")
# ---------------------------------------------------------------------------
from web_search import web_search

result = web_search("")
check("empty query returns an empty result with an error, doesn't raise",
      result["results"] == [] and "error" in result, f"got: {result}")

# ---------------------------------------------------------------------------
section("3. Live DuckDuckGo search (default path, zero API key)")
# ---------------------------------------------------------------------------
try:
    import requests as _r
    _r.get("https://html.duckduckgo.com/html/", timeout=5)
    internet_up = True
except Exception:
    internet_up = False

if not internet_up:
    print("  [SKIP] No internet reachability — skipping live search checks.")
else:
    result = web_search("Python programming language official site", max_results=5)
    check("live search used the duckduckgo source (no BRAVE_SEARCH_API_KEY set in this test run)",
          result.get("source") == "duckduckgo", f"got source: {result.get('source')}")
    check("live search returns at least one real result", len(result.get("results", [])) > 0,
          f"got: {result}")
    if result.get("results"):
        first = result["results"][0]
        check("each result has title/url/snippet keys",
              all(k in first for k in ("title", "url", "snippet")), f"got: {first}")
        check("result URLs look like real URLs", first["url"].startswith(("http://", "https://")),
              f"got: {first['url']!r}")
        check("result titles are non-empty", len(first["title"]) > 0)

    result_capped = web_search("machine learning", max_results=2)
    check("max_results is actually respected", len(result_capped.get("results", [])) <= 2,
          f"got {len(result_capped.get('results', []))} results")

    # Regression check: DDG's snippet HTML bolds matched keywords with no surrounding
    # whitespace in the text nodes — without separator=" " in get_text(), words run
    # together ("tophomenetworksecurity...") instead of being readable. Search a term
    # likely to have several keyword matches bolded within one snippet to catch this.
    result_spacing = web_search("home network security best practices", max_results=3)
    long_snippets = [r["snippet"] for r in result_spacing.get("results", []) if len(r["snippet"]) > 40]
    check("snippets have real word boundaries, not run-together bolded-keyword text",
          long_snippets and all(" " in s for s in long_snippets), f"got: {long_snippets}")

# ---------------------------------------------------------------------------
section("4. Brave Search path — used only when BRAVE_SEARCH_API_KEY is actually set")
# ---------------------------------------------------------------------------
import unittest.mock as mock
import os as _os

with mock.patch.dict(_os.environ, {"BRAVE_SEARCH_API_KEY": "fake-key-for-this-test"}):
    with mock.patch("requests.get") as mock_get:
        mock_get.side_effect = Exception("simulated network failure")
        result = web_search("test query")
        check("when BRAVE_SEARCH_API_KEY is set, the brave path is used instead of duckduckgo",
              result.get("source") == "brave", f"got: {result}")
        check("a Brave API failure is reported cleanly, not raised", "error" in result, f"got: {result}")


print(f"\n{'=' * 55}\n{PASS} passed, {FAIL} failed\n{'=' * 55}")
sys.exit(1 if FAIL else 0)

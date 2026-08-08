"""
Tests for Phase 7: usage_tracker.py — approximate token/cost tracking, and its wiring into
session_manager.py's process_turn_stream.

Run: python test_usage_tracker_phase7.py
"""

import os
import sys
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


import usage_tracker as ut

# ---------------------------------------------------------------------------
section("1. estimate_tokens")
# ---------------------------------------------------------------------------
check("empty string is 0 tokens", ut.estimate_tokens("") == 0)
check("None doesn't raise, treated as 0", ut.estimate_tokens(None) == 0)
check("roughly chars/4", ut.estimate_tokens("x" * 400) == 100)

# ---------------------------------------------------------------------------
section("2. Pricing table sanity")
# ---------------------------------------------------------------------------
check("ollama is always free regardless of 'model'", ut._rate_for("ollama", "whatever-model") == (0.0, 0.0))
check("known openai model has non-zero pricing", ut._rate_for("openai", "gpt-4o") != (0.0, 0.0))
check("unknown provider/model falls back to (0.0, 0.0), not an error",
      ut._rate_for("not-a-real-provider", "not-a-real-model") == (0.0, 0.0))

# ---------------------------------------------------------------------------
section("3. record_usage + get_usage_stats, isolated from real usage_stats.json")
# ---------------------------------------------------------------------------
tmp_dir = tempfile.mkdtemp(prefix="jarvis_test_usage_")
orig_usage_file = ut.USAGE_FILE
ut.USAGE_FILE = os.path.join(tmp_dir, "test_usage.json")

try:
    entry = ut.record_usage("ollama", "x" * 400, "y" * 200)
    check("record_usage returns a real entry", entry.get("provider") == "ollama" and entry.get("input_tokens") == 100)
    check("ollama usage always costs $0", entry.get("cost_usd") == 0.0, f"got: {entry}")

    entry2 = ut.record_usage("openai", "x" * 4000, "y" * 4000)
    check("openai usage accrues a non-zero cost", entry2.get("cost_usd") > 0, f"got: {entry2}")

    stats = ut.get_usage_stats(days=30)
    check("get_usage_stats sees both providers", set(stats["by_provider"].keys()) == {"ollama", "openai"},
          f"got: {stats['by_provider'].keys()}")
    check("ollama total cost is still $0 after aggregation", stats["by_provider"]["ollama"]["cost_usd"] == 0.0)
    check("total_cost_usd matches the sum across providers",
          abs(stats["total_cost_usd"] - stats["by_provider"]["openai"]["cost_usd"]) < 1e-9, f"got: {stats}")

    # Recording again for the same provider on the same day should accumulate, not overwrite.
    ut.record_usage("ollama", "x" * 400, "y" * 400)
    stats2 = ut.get_usage_stats(days=30)
    check("repeated calls the same day accumulate token counts rather than overwriting",
          stats2["by_provider"]["ollama"]["input_tokens"] == 200, f"got: {stats2['by_provider']['ollama']}")
    check("call count increments too", stats2["by_provider"]["ollama"]["calls"] == 2)

    # days filtering — inject an old-dated entry directly and confirm it's excluded from a
    # short window but included in a long one.
    raw = ut._load()
    raw["2000-01-01"] = {"anthropic": {"model": "x", "input_tokens": 1000, "output_tokens": 1000, "cost_usd": 5.0, "calls": 1}}
    ut._save(raw)

    recent_stats = ut.get_usage_stats(days=7)
    check("an entry from 2000 doesn't show up in a 7-day window",
          "anthropic" not in recent_stats["by_provider"], f"got: {recent_stats['by_provider'].keys()}")

    long_stats = ut.get_usage_stats(days=100000)
    check("the old entry does show up once the window is wide enough",
          "anthropic" in long_stats["by_provider"], f"got: {long_stats['by_provider'].keys()}")

    empty_file = os.path.join(tmp_dir, "nonexistent.json")
    ut.USAGE_FILE = empty_file
    fresh_stats = ut.get_usage_stats()
    check("no usage file yet returns a clean zeroed-out result, not an error",
          fresh_stats["total_cost_usd"] == 0.0 and fresh_stats["by_provider"] == {})

finally:
    ut.USAGE_FILE = orig_usage_file
    shutil.rmtree(tmp_dir, ignore_errors=True)

# ---------------------------------------------------------------------------
section("4. Tool registration")
# ---------------------------------------------------------------------------
from tools import ALL_TOOLS, Tier, REQUIRES_CAPABILITY
check("get_usage_stats registered", "get_usage_stats" in ALL_TOOLS)
if "get_usage_stats" in ALL_TOOLS:
    check("Tier 1 (read-only)", ALL_TOOLS["get_usage_stats"].tier == Tier.TIER_1)
check("no capability gate needed (reads a small local JSON stats file, like conversation search)",
      "get_usage_stats" not in REQUIRES_CAPABILITY)

# ---------------------------------------------------------------------------
section("5. End-to-end — process_turn_stream actually records usage for a real turn")
# ---------------------------------------------------------------------------
from session_manager import JarvisBrain
from memory import Memory

tmp_dir2 = tempfile.mkdtemp(prefix="jarvis_test_usage_e2e_")
ut.USAGE_FILE = os.path.join(tmp_dir2, "e2e_usage.json")

try:
    class FakeProvider:
        def chat_stream(self, messages, system_prompt):
            return iter(["Hello ", "Devin, ", "all set."])

    mem = Memory(data_dir=os.path.join(tmp_dir2, "mem"))
    brain = JarvisBrain(approval_fn=lambda a, t: True, memory=mem)
    brain._brain = FakeProvider()
    brain._llm_error = None

    import unittest.mock as mock
    with mock.patch.dict(os.environ, {"ACTIVE_LLM": "ollama"}):
        list(brain.process_turn_stream([{"role": "user", "content": "hi"}]))

    stats = ut.get_usage_stats()
    check("a real turn through process_turn_stream actually recorded usage",
          "ollama" in stats["by_provider"] and stats["by_provider"]["ollama"]["calls"] == 1,
          f"got: {stats}")
finally:
    ut.USAGE_FILE = orig_usage_file
    shutil.rmtree(tmp_dir2, ignore_errors=True)

print(f"\n{'=' * 55}\n{PASS} passed, {FAIL} failed\n{'=' * 55}")
sys.exit(1 if FAIL else 0)

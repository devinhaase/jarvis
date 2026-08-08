"""
Tests for Phase 6 item 2: deeper self-learning, Option A (confirmed with Devin over
Option B's local fine-tuning) — skill usage-weighting (skills.py) and the periodic
tool-pair pattern scan (deep_reflection.py).

Required test coverage: confirm the approach still can't touch permission tiers or the
authorization list — section 5 does this two ways: (1) static source inspection (the
module literally never references Tier, approve_skill, or authorized_targets.json), and
(2) a runtime check that running a full scan doesn't change a single tool's .tier or the
authorized_targets.json file's content.

Run: python test_deep_reflection_phase6.py
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


# ---------------------------------------------------------------------------
section("1. Skill.weight() and proven-skill labeling")
# ---------------------------------------------------------------------------

import skills

_real_skills_dir = skills.SKILLS_DIR
_tmp_skills_dir = tempfile.mkdtemp()
skills.SKILLS_DIR = _tmp_skills_dir

try:
    fresh = skills.Skill(name="fresh", success_count=0, fail_count=0)
    check("a skill with zero outcomes has weight 0 (neither promoted nor buried)", fresh.weight() == 0.0)

    proven = skills.Skill(name="proven", success_count=20, fail_count=1)
    shaky = skills.Skill(name="shaky", success_count=2, fail_count=1)
    check("a skill proven over many runs outweighs one with a similar ratio but few runs",
          proven.weight() > shaky.weight(), f"proven={proven.weight()}, shaky={shaky.weight()}")

    mostly_failing = skills.Skill(name="bad", success_count=1, fail_count=10)
    check("a mostly-failing skill has negative weight", mostly_failing.weight() < 0)

    check("'[proven]' label appears for a high-weight skill", "[proven" in proven.as_prompt_blurb(), f"got: {proven.as_prompt_blurb()}")
    check("'[proven]' label does NOT appear for a fresh skill", "[proven" not in fresh.as_prompt_blurb(), f"got: {fresh.as_prompt_blurb()}")

    section("2. active_skills_for_team() sorts by weight, doesn't just return insertion order")
    skills.propose_skill("low weight skill", "rarely useful", ["step"], ["check_system_health"], team="it")
    skills.approve_skill("low weight skill")
    skills.record_skill_outcome("low weight skill", success=True)  # 1 success -> small positive weight

    skills.propose_skill("high weight skill", "very useful", ["step"], ["check_system_health"], team="it")
    skills.approve_skill("high weight skill")
    for _ in range(15):
        skills.record_skill_outcome("high weight skill", success=True)  # 15 successes -> much bigger weight

    ordered = skills.active_skills_for_team("it")
    names_in_order = [s.name for s in ordered]
    check("the proven (higher-weight) skill sorts before the barely-used one",
          names_in_order.index("high weight skill") < names_in_order.index("low weight skill"),
          f"got order: {names_in_order}")

    section("3. Flagging still excludes from active_skills_for_team, weighting doesn't override that")
    for _ in range(6):
        skills.record_skill_outcome("low weight skill", success=False)  # push it into flagged territory
    flagged_check = skills.load_skill("low weight skill")
    check("the skill got flagged from repeated failure", flagged_check.flagged is True, f"got: {flagged_check.recent_outcomes}")
    check("a flagged skill is excluded from active_skills_for_team regardless of weight",
          "low weight skill" not in [s.name for s in skills.active_skills_for_team("it")])
finally:
    skills.SKILLS_DIR = _real_skills_dir
    shutil.rmtree(_tmp_skills_dir, ignore_errors=True)

# ---------------------------------------------------------------------------
section("4. deep_reflection.py — clustering and pattern detection")
# ---------------------------------------------------------------------------

import deep_reflection as dr

episodes = [
    {"timestamp": 1000, "action": "check_system_health", "result": "ok", "tier": "TIER_1"},
    {"timestamp": 1005, "action": "scan_local_logs", "result": "ok", "tier": "TIER_1"},   # same cluster as above (5s gap)
    {"timestamp": 2000, "action": "check_system_health", "result": "ok", "tier": "TIER_1"},  # new cluster (995s gap)
    {"timestamp": 2003, "action": "scan_local_logs", "result": "ok", "tier": "TIER_1"},
    {"timestamp": 3000, "action": "web_search", "result": "ok", "tier": "TIER_1"},  # solo call, own cluster
    {"timestamp": 4000, "action": "check_system_health", "result": "ok", "tier": "TIER_1"},
    {"timestamp": 4002, "action": "scan_local_logs", "result": "ok", "tier": "TIER_1"},
]
clusters = dr._cluster_episodes(episodes, gap_seconds=30)
check("clustering groups nearby calls, splits on a big time gap", len(clusters) == 4, f"got {len(clusters)} clusters: {clusters}")
check("first cluster has both tools", {e['action'] for e in clusters[0]} == {"check_system_health", "scan_local_logs"})
check("solo web_search is its own cluster", len(clusters[2]) == 1 and clusters[2][0]["action"] == "web_search")

# find_recurring_tool_patterns() reads from the REAL Memory() episodic log by default —
# test the pure counting logic directly against our own clusters here (the isolated,
# monkeypatched-Memory version is exercised end-to-end in section 5 below).
from collections import Counter as _Counter
pair_counts = _Counter()
for cluster in clusters:
    tools_in_cluster = sorted(set(e["action"] for e in cluster))
    if len(tools_in_cluster) < 2:
        continue
    for i in range(len(tools_in_cluster)):
        for j in range(i + 1, len(tools_in_cluster)):
            pair_counts[(tools_in_cluster[i], tools_in_cluster[j])] += 1
check("the recurring pair (check_system_health, scan_local_logs) was counted 3 times",
      pair_counts[("check_system_health", "scan_local_logs")] == 3, f"got: {dict(pair_counts)}")

# ---------------------------------------------------------------------------
section("5. run_deep_pattern_scan() — proposes via the same gate, never activates, never")
section("   touches tiers or the authorization list")
# ---------------------------------------------------------------------------

_real_skills_dir2 = skills.SKILLS_DIR
_tmp_skills_dir2 = tempfile.mkdtemp()
skills.SKILLS_DIR = _tmp_skills_dir2

_real_data_dir = os.path.join("data")
_tmp_data_dir = tempfile.mkdtemp()

import memory as memory_module
_real_memory_cls_default = memory_module.Memory.__init__.__defaults__


class _IsolatedMemory(memory_module.Memory):
    def __init__(self, data_dir=_tmp_data_dir):
        super().__init__(data_dir=data_dir)


_real_Memory = memory_module.Memory
memory_module.Memory = _IsolatedMemory
dr_memory_ref = None

try:
    seed = _IsolatedMemory()
    # log_episode() always stamps with the real wall clock, no way to inject a controlled
    # timestamp through the public API — and back-to-back real calls land well under
    # CLUSTER_GAP_SECONDS apart, which would merge every seeded pair into ONE cluster
    # instead of the several separate ones this test needs. Write the episodic log
    # directly instead, with real, deliberately-spaced timestamps (30s+ / gap_seconds
    # apart -> distinct clusters), same file format log_episode itself writes.
    now = time.time()
    with open(seed.episodic_file, "w", encoding="utf-8") as f:
        for i in range(dr.MIN_OCCURRENCES + 1):
            base = now + i * 100  # 100s apart -> always a fresh cluster per iteration
            for action in ("check_system_health", "scan_local_logs"):
                f.write(json.dumps({"timestamp": base, "action": action, "result": "ok", "tier": "TIER_1"}) + "\n")
                base += 2  # 2s apart -> same cluster as its pair

    class _FakeProvider:
        def __init__(self, response_text):
            self.response_text = response_text

        def chat(self, messages, system_prompt):
            return {"response": self.response_text, "tools": []}

    class _FakeBrain:
        def __init__(self, response_text):
            self._brain = _FakeProvider(response_text)

    # 5a. Model says this IS a real pattern -> proposes a skill, status=proposed.
    brain_yes = _FakeBrain(
        '[PATTERN_SKILL: name="health and log check" when="a routine system check is needed" steps="check health|scan logs"]'
    )
    result = dr.run_deep_pattern_scan(brain_yes)
    check("a recurring pattern was found", result["candidates_found"] >= 1, f"got: {result}")
    check("a skill was proposed", result["skill_proposed"] is not None, f"got: {result}")
    if result["skill_proposed"]:
        check("the proposed skill's status is 'proposed', never 'active'", result["skill_proposed"].status == "proposed")

    # 5b. Re-running the scan should NOT re-propose the same now-covered pattern.
    result2 = dr.run_deep_pattern_scan(brain_yes)
    check("re-scanning doesn't re-propose a pattern already covered by an existing skill",
          result2["skill_proposed"] is None, f"got: {result2}")

    # 5c. Model says "none" -> no skill proposed, for a fresh isolated skills dir.
    skills.SKILLS_DIR = tempfile.mkdtemp()  # fresh dir so the pattern isn't "already covered"
    brain_no = _FakeBrain('[PATTERN_SKILL: none]')
    result3 = dr.run_deep_pattern_scan(brain_no)
    check("the model declining the pattern produces no skill proposal", result3["skill_proposed"] is None, f"got: {result3}")

    # --- The non-negotiable: structural + runtime guarantee ---
    # Checks actual usage patterns (a real import or call), not bare substrings — the
    # module's own docstrings/comments mention these names in prose precisely to explain
    # this guarantee, which would false-positive a naive "not in source" check.
    with open("deep_reflection.py", encoding="utf-8") as f:
        source = f.read()
    import re as _re
    check("deep_reflection.py never calls approve_skill(...)", not _re.search(r'\bapprove_skill\s*\(', source))
    # Anchored to actual line-start import statements (^\s*import.../^\s*from...import...)
    # so this doesn't false-positive on the module's own docstring prose ("does not import
    # Tier") — a real import is a statement, not a phrase that happens to contain the words.
    check("deep_reflection.py never imports Tier from tools",
          not _re.search(r'^\s*(from\s+\S+\s+)?import\s+.*\bTier\b', source, _re.MULTILINE))
    check("deep_reflection.py never opens authorized_targets.json", not _re.search(r'open\([^)]*authorized_targets', source))

    from tools import ALL_TOOLS
    tiers_before = {name: t.tier for name, t in ALL_TOOLS.items()}
    auth_path = os.path.join("data", "authorized_targets.json")
    auth_before = None
    if os.path.exists(auth_path):
        with open(auth_path, encoding="utf-8") as f:
            auth_before = f.read()

    dr.run_deep_pattern_scan(brain_yes)  # run it again for good measure

    tiers_after = {name: t.tier for name, t in ALL_TOOLS.items()}
    check("no tool's tier changed after running a deep pattern scan", tiers_before == tiers_after)
    if auth_before is not None:
        with open(auth_path, encoding="utf-8") as f:
            auth_after = f.read()
        check("authorized_targets.json is byte-for-byte unchanged after a deep pattern scan", auth_before == auth_after)

finally:
    memory_module.Memory = _real_Memory
    skills.SKILLS_DIR = _real_skills_dir2
    shutil.rmtree(_tmp_skills_dir2, ignore_errors=True)
    shutil.rmtree(_tmp_data_dir, ignore_errors=True)


print(f"\n{'=' * 55}\n{PASS} passed, {FAIL} failed\n{'=' * 55}")
sys.exit(1 if FAIL else 0)

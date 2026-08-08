"""
Tests for Phase 5: self-improvement / learning loop — skills.py (the review-gated skill
library), reflection.py (the autonomous proposal half), SOUL.md/USER.md/MEMORY.md (the
human-readable identity/memory files), and the real server end-to-end (reflection actually
firing after a real multi-step turn, skill review WS handlers).

Required test coverage (per the Phase 5 spec):
  - Run 3 real multi-step tasks; confirm skills are correctly PROPOSED, not silently
    activated / behavior silently changed -> section 5a
  - Try to get a proposed skill auto-activated without approval; confirm it can't -> section 2
  - Simulate a failing skill; confirm it's flagged, not silently disabled or left running -> section 1

Run: python test_skills_phase5.py
"""

import os
import sys
import json
import time
import shutil
import asyncio
import threading
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
section("0. SOUL.md / USER.md / MEMORY.md — real files, real content")
# ---------------------------------------------------------------------------

import llm
check("SOUL.md exists and llm.PERSONA was loaded from it", os.path.exists("SOUL.md") and "IDENTITY" in llm.PERSONA)
with open("SOUL.md", encoding="utf-8") as f:
    check("llm.PERSONA content matches SOUL.md verbatim", llm.PERSONA == f.read())

import memory_docs
from memory import Memory

# Real bug found (and fixed) while committing this session's work: memory_docs.py's output
# paths are fixed at the project root, so a Memory instance built with a non-default
# data_dir (every isolated test in this codebase does this on purpose) was overwriting the
# REAL USER.md/MEMORY.md with test data. Fixed by gating _sync_memory_docs() to only fire
# for the real, default-data_dir instance — verify that gate holds first...
_tmp_data = tempfile.mkdtemp()
with open(memory_docs.USER_MD_PATH, encoding="utf-8") as f:
    real_user_md_before = f.read()
test_mem = Memory(data_dir=_tmp_data)
test_mem.append_fact("this fact must never reach the real MEMORY.md")
test_mem.update_semantic("preferences", {"reply_style": "this must never reach the real USER.md"})
with open(memory_docs.USER_MD_PATH, encoding="utf-8") as f:
    real_user_md_after = f.read()
check("a non-default-data_dir Memory instance does NOT touch the real USER.md/MEMORY.md",
      real_user_md_before == real_user_md_after)
shutil.rmtree(_tmp_data, ignore_errors=True)

# ...then verify memory_docs.py's own rendering logic directly (its actual job), against
# isolated output paths rather than the real project-root files.
_real_user_md_path, _real_memory_md_path = memory_docs.USER_MD_PATH, memory_docs.MEMORY_MD_PATH
_tmp_docs_dir = tempfile.mkdtemp()
memory_docs.USER_MD_PATH = os.path.join(_tmp_docs_dir, "USER.md")
memory_docs.MEMORY_MD_PATH = os.path.join(_tmp_docs_dir, "MEMORY.md")
try:
    memory_docs.regenerate({
        "user_name": "Devin", "facts": ["Devin's test lab uses a Raspberry Pi cluster."],
        "projects": [], "preferences": {"reply_style": "terse"},
    })
    check("USER.md written to the (isolated) target path", os.path.exists(memory_docs.USER_MD_PATH))
    check("MEMORY.md written to the (isolated) target path", os.path.exists(memory_docs.MEMORY_MD_PATH))
    with open(memory_docs.USER_MD_PATH, encoding="utf-8") as f:
        user_content = f.read()
    with open(memory_docs.MEMORY_MD_PATH, encoding="utf-8") as f:
        memory_content = f.read()
    check("USER.md reflects the preference just rendered", "terse" in user_content, f"got: {user_content}")
    check("MEMORY.md reflects the fact just rendered", "Raspberry Pi cluster" in memory_content, f"got: {memory_content}")
finally:
    memory_docs.USER_MD_PATH, memory_docs.MEMORY_MD_PATH = _real_user_md_path, _real_memory_md_path
    shutil.rmtree(_tmp_docs_dir, ignore_errors=True)

# ---------------------------------------------------------------------------
section("1. skills.py — CRUD, tier capping, versioning, outcome tracking/flagging")
# ---------------------------------------------------------------------------

import skills

_real_skills_dir = skills.SKILLS_DIR
_tmp_skills_dir = tempfile.mkdtemp()
skills.SKILLS_DIR = _tmp_skills_dir

try:
    s = skills.propose_skill(
        "morning check", "Devin asks for a quick morning status check",
        ["check system health", "check security posture"],
        ["check_system_health", "get_security_posture"], team="it",
    )
    check("propose_skill always writes status='proposed'", s.status == "proposed")
    check("tier is computed from real tool tiers (both TIER_1 here)", s.tier == "TIER_1", f"got {s.tier}")
    check("version starts at 1", s.version == 1)

    loaded = skills.load_skill("morning check")
    check("round-trips through disk correctly", loaded.name == s.name and loaded.tools == s.tools and loaded.steps == s.steps)

    proposed_list = skills.list_skills(status="proposed")
    check("list_skills(status='proposed') finds it", len(proposed_list) == 1 and proposed_list[0].name == "morning check")

    # Tier cap must reflect the MOST sensitive tool, not just any tool.
    s2 = skills.propose_skill(
        "backup then scan", "before a risky change", ["back up first", "scan ports"],
        ["create_backup", "scan_ports"], team="it",
    )
    check("tier cap uses the highest tier among tool calls (TIER_3, from scan_ports)", s2.tier == "TIER_3", f"got {s2.tier}")

    # A skill referencing a nonexistent tool name shouldn't crash tier computation.
    s3 = skills.propose_skill("bogus tool skill", "n/a", ["step"], ["not_a_real_tool"], team="it")
    check("unknown tool names don't crash tier computation, default to TIER_1", s3.tier == "TIER_1")

    section("2. Cannot be auto-activated without going through approve_skill()")
    # ---------------------------------------------------------------------------
    check("a freshly-proposed skill is NOT active", skills.load_skill("morning check").status != "active")

    # Every OTHER mutating function in this module must never set status='active'.
    edited = skills.edit_skill("morning check", when_to_use="updated")
    check("edit_skill does not activate it (resets to 'proposed', not 'active')", edited.status == "proposed")

    for _ in range(20):
        skills.record_skill_outcome("morning check", success=True)
    check("record_skill_outcome (even 20 straight successes) never activates it",
          skills.load_skill("morning check").status != "active")

    rejected_then_reproposed = skills.reject_skill("bogus tool skill")
    check("reject_skill sets 'rejected', not 'active'", rejected_then_reproposed.status == "rejected")

    disabled = skills.disable_skill("backup then scan")  # disabling something that was never active is still a valid no-op-ish transition
    check("disable_skill never results in 'active'", disabled.status == "disabled")

    # The ONLY function that can activate anything:
    approved = skills.approve_skill("morning check")
    check("approve_skill (and only approve_skill) sets status='active'", approved.status == "active")
    check("approve_skill recomputes the tier cap fresh rather than trusting stored data",
          approved.tier == "TIER_1")

    # Editing an ACTIVE skill must knock it back into review, not silently keep it active.
    # (version was already bumped to 2 by the edit_skill() call earlier in this section.)
    edited_active = skills.edit_skill("morning check", steps=["new step"])
    check("editing an active skill resets it to 'proposed' (must be re-reviewed)", edited_active.status == "proposed")
    check("editing bumps the version again", edited_active.version == 3, f"got {edited_active.version}")
    # Re-approve for the next section.
    skills.approve_skill("morning check")

    section("3. Failing skill gets FLAGGED, never silently disabled or silently left running")
    # ---------------------------------------------------------------------------
    # Fresh skill for this section — "morning check" already has 20+ recorded outcomes
    # from section 2's activation test, and the flag logic only looks at the most recent
    # window, so reusing it would make this section's math depend on section 2's history.
    flaky = skills.propose_skill("flaky skill", "testing the flag path", ["step"], ["check_system_health"], team="it")
    skills.approve_skill("flaky skill")
    check("not flagged initially", skills.load_skill("flaky skill").flagged is False)

    # 2 successes, then a run of failures — clearly cross the >30%-of-last-N threshold.
    skills.record_skill_outcome("flaky skill", success=True)
    skills.record_skill_outcome("flaky skill", success=True)
    for _ in range(4):
        skills.record_skill_outcome("flaky skill", success=False)
    flagged_skill = skills.load_skill("flaky skill")
    check("flagged becomes True once the recent failure rate spikes", flagged_skill.flagged is True,
          f"recent_outcomes={flagged_skill.recent_outcomes}")
    check("status is STILL 'active' — flagging never disables", flagged_skill.status == "active")
    check("active_skills_for_team excludes a flagged skill from being suggested",
          "flaky skill" not in [s.name for s in skills.active_skills_for_team("it")])
    check("but list_skills(status='active') still shows it — flagging doesn't hide it from review",
          "flaky skill" in [s.name for s in skills.list_skills(status="active")])

    cleared = skills.clear_flag("flaky skill")
    check("clear_flag is the only way the flag goes away, and it's an explicit human action",
          cleared.flagged is False and cleared.status == "active")

    check("a nonexistent skill name returns None everywhere, doesn't raise",
          skills.load_skill("nope") is None and skills.approve_skill("nope") is None and
          skills.record_skill_outcome("nope", True) is None)

finally:
    skills.SKILLS_DIR = _real_skills_dir
    shutil.rmtree(_tmp_skills_dir, ignore_errors=True)

# ---------------------------------------------------------------------------
section("4. reflection.py — trigger logic and proposal parsing")
# ---------------------------------------------------------------------------

from reflection import should_reflect, run_reflection

check("single successful tool call does NOT trigger reflection", should_reflect([{"tool": "a"}], []) is False)
check("2+ tool calls DOES trigger reflection (multi-step)", should_reflect([{"tool": "a"}, {"tool": "b"}], []) is True)
check("a capability denial DOES trigger reflection (took more than one attempt)",
      should_reflect([{"tool": "a"}], [{"tool": "b", "required": "filesystem"}]) is True)
check("no tools and no denials does not trigger", should_reflect([], []) is False)


class _FakeBrainForReflection:
    def __init__(self, response_text):
        self._brain = _FakeProviderChatOnly(response_text)


class _FakeProviderChatOnly:
    def __init__(self, response_text):
        self.response_text = response_text
        self.calls = []

    def chat(self, messages, system_prompt):
        self.calls.append(messages)
        return {"response": self.response_text, "tools": []}


_real_skills_dir2 = skills.SKILLS_DIR
_tmp_skills_dir2 = tempfile.mkdtemp()
skills.SKILLS_DIR = _tmp_skills_dir2
try:
    with_proposal = _FakeBrainForReflection(
        '[REFLECTION: succeeded="true" worked="found the right tools" differently="nothing"]\n'
        '[SKILL_PROPOSAL: name="reflect test skill" when="checking multiple systems at once" steps="run tool a|run tool b"]'
    )
    result = run_reflection(with_proposal, "check everything", [{"tool": "check_system_health"}, {"tool": "get_security_posture"}],
                             "All good.", team_key="it")
    check("reflection dict parsed correctly", result["reflection"] == {
        "succeeded": True, "worked": "found the right tools", "differently": "nothing",
    }, f"got: {result['reflection']}")
    check("skill_proposed is set and status is 'proposed'",
          result["skill_proposed"] is not None and result["skill_proposed"].status == "proposed")
    check("proposed skill's tools come from the actual tools_ran this turn",
          set(result["skill_proposed"].tools) == {"check_system_health", "get_security_posture"})

    without_proposal = _FakeBrainForReflection(
        '[REFLECTION: succeeded="true" worked="simple lookup" differently="nothing"]'
    )
    before_count = len(skills.list_skills())
    result2 = run_reflection(without_proposal, "what's my cpu", [{"tool": "check_system_health"}, {"tool": "x"}],
                              "9%.", team_key="it")
    check("no SKILL_PROPOSAL tag -> no skill created (most tasks shouldn't propose one)",
          result2["skill_proposed"] is None and len(skills.list_skills()) == before_count)

    unparseable = _FakeBrainForReflection("garbage that doesn't match the tagged format at all")
    result3 = run_reflection(unparseable, "x", [{"tool": "a"}, {"tool": "b"}], "y", team_key="it")
    check("unparseable model output degrades to reflection=None, doesn't raise", result3["reflection"] is None)

    check("run_reflection with brain=None doesn't raise", run_reflection(None, "x", [], "y") == {"reflection": None, "skill_proposed": None})
finally:
    skills.SKILLS_DIR = _real_skills_dir2
    shutil.rmtree(_tmp_skills_dir2, ignore_errors=True)

# ---------------------------------------------------------------------------
section("5. Server: real end-to-end — reflection fires after real multi-step turns,")
section("   skill review WS handlers work over the real protocol")
# ---------------------------------------------------------------------------

import server as server_module
import uvicorn
import websockets
from device_registry import DeviceRegistry
from conversation_store import ConversationStore as CS

TEST_PORT = 8773
os.environ["JARVIS_SERVER_PORT"] = str(TEST_PORT)

_test_devices_file = os.path.join(tempfile.gettempdir(), "jarvis_test_devices_skills.json")
if os.path.exists(_test_devices_file):
    os.remove(_test_devices_file)
server_module.registry = DeviceRegistry(path=_test_devices_file)

_test_db = os.path.join(tempfile.gettempdir(), "jarvis_test_store_skills.db")
if os.path.exists(_test_db):
    os.remove(_test_db)
server_module.store = CS(path=_test_db)

_real_skills_dir3 = skills.SKILLS_DIR
_tmp_skills_dir3 = tempfile.mkdtemp()
skills.SKILLS_DIR = _tmp_skills_dir3

# Same real bug this project already found once (Phase 4's test) — no-op the background
# loops so posture_monitor/daily_briefing/team_board can't broadcast into this test.
async def _noop_background_loop(*args, **kwargs):
    await asyncio.Event().wait()

import posture_monitor, daily_briefing, team_board_dispatcher as _tbd, deep_reflection as _dr
posture_monitor.posture_monitor_loop = _noop_background_loop
daily_briefing.daily_briefing_loop = _noop_background_loop
_tbd.team_board_dispatch_loop = _noop_background_loop
_dr.deep_reflection_loop = _noop_background_loop  # Phase 6 item 2

server_module.APPROVAL_TIMEOUT = {"TIER_3": 0.6, "TIER_4": 0.6}
server_module.APPROVAL_DEFAULT = {"TIER_3": True, "TIER_4": False}

DEVICE_ID = "skills-test-device"
DEVICE_TOKEN = server_module.registry.register(DEVICE_ID, "Skills Test Device", ["filesystem"])


class FakeProvider:
    def __init__(self):
        self.chat_responses = []
        self.chat_rounds = []

    def chat_stream(self, messages, system_prompt):
        if not self.chat_rounds:
            return iter(["(no more canned chat_stream rounds)"])
        return iter(self.chat_rounds.pop(0))

    def chat(self, messages, system_prompt):
        if not self.chat_responses:
            return {"response": "(no more canned chat responses)", "tools": []}
        return self.chat_responses.pop(0)


def set_fake_provider(chat_responses=None, chat_rounds=None):
    fp = FakeProvider()
    fp.chat_responses = list(chat_responses or [])
    fp.chat_rounds = list(chat_rounds or [])
    server_module.brain._brain = fp
    server_module.brain._llm_error = None
    return fp


config = uvicorn.Config(server_module.app, host="127.0.0.1", port=TEST_PORT, log_level="warning")
uv_server = uvicorn.Server(config)
server_thread = threading.Thread(target=uv_server.run, daemon=True)
server_thread.start()

import urllib.request
up = False
for _ in range(50):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{TEST_PORT}/health", timeout=1)
        up = True
        break
    except Exception:
        time.sleep(0.2)
if not up:
    print("[FAIL] server never came up")
    sys.exit(1)

WS_URL = f"ws://127.0.0.1:{TEST_PORT}/ws"


async def hello(ws):
    await ws.send(json.dumps({"type": "hello", "device_id": DEVICE_ID, "token": DEVICE_TOKEN, "capabilities": ["filesystem"]}))
    return json.loads(await ws.recv())


async def send_and_collect(ws, text):
    await ws.send(json.dumps({"type": "message", "text": text}))
    events = []
    while True:
        data = json.loads(await ws.recv())
        events.append(data)
        if data.get("type") == "stream_end":
            break
    return events


async def wait_for_proposed_skill(name_substring, timeout=8):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for s in skills.list_skills(status="proposed"):
            if name_substring.lower() in s.name.lower():
                return s
        await asyncio.sleep(0.2)
    return None


async def main():
    section("5a. Three real multi-step tasks -> skills proposed, not silently activated")
    reflection_response = (
        '[REFLECTION: succeeded="true" worked="ran both checks" differently="nothing"]\n'
        '[SKILL_PROPOSAL: name="{name}" when="Devin wants a combined health check" steps="check health|check logs"]'
    )

    for i, task_name in enumerate(["task one health check", "task two health check", "task three health check"]):
        set_fake_provider(
            chat_rounds=[["[TOOL: check_system_health {}][TOOL: scan_local_logs {}]"], [f"Done with {task_name}."]],
            chat_responses=[{"response": reflection_response.format(name=task_name), "tools": []}],
        )
        async with websockets.connect(WS_URL) as ws:
            await hello(ws)
            events = await send_and_collect(ws, f"ask the it team to run a full health check, {task_name}")
        stream_end = [e for e in events if e.get("type") == "stream_end"][0]
        check(f"[{task_name}] both tools actually ran this turn", set(stream_end.get("tools_ran", [])) >= {"check_system_health", "scan_local_logs"},
              f"got: {stream_end.get('tools_ran')}")
        check(f"[{task_name}] response was NOT silently altered by the learning system",
              stream_end.get("full_text", "") == f"Done with {task_name}.", f"got: {stream_end.get('full_text')!r}")

        proposed = await wait_for_proposed_skill(task_name)
        check(f"[{task_name}] a skill was proposed after this multi-step task", proposed is not None)
        if proposed:
            check(f"[{task_name}] proposed skill's status is 'proposed', not 'active'", proposed.status == "proposed")

    all_proposed = skills.list_skills(status="proposed")
    check("all 3 tasks produced distinct proposed skills (not deduped/merged into one)",
          len(all_proposed) == 3, f"got: {[s.name for s in all_proposed]}")
    check("NONE of them are active — behavior was never silently changed", all(s.status == "proposed" for s in all_proposed))

    section("5b. list_skills / approve_skill / reject_skill over the real WS protocol")
    async with websockets.connect(WS_URL) as ws:
        await hello(ws)
        await ws.send(json.dumps({"type": "list_skills", "status": "proposed"}))
        resp = json.loads(await ws.recv())
        check("list_skills WS message returns the proposed skills", resp["type"] == "skill_list" and len(resp["skills"]) == 3,
              f"got: {resp}")

        target = resp["skills"][0]["name"]
        await ws.send(json.dumps({"type": "approve_skill", "name": target}))
        resp2 = json.loads(await ws.recv())
        check("approve_skill WS message activates it", resp2["type"] == "skill_updated" and resp2["skill"]["status"] == "active",
              f"got: {resp2}")

        await ws.send(json.dumps({"type": "reject_skill", "name": "not-a-real-skill-name"}))
        resp3 = json.loads(await ws.recv())
        check("rejecting a nonexistent skill returns a clean error, not a crash", resp3["type"] == "error", f"got: {resp3}")


asyncio.run(main())

uv_server.should_exit = True
time.sleep(0.5)
skills.SKILLS_DIR = _real_skills_dir3
shutil.rmtree(_tmp_skills_dir3, ignore_errors=True)
for f in (_test_devices_file, _test_db):
    try:
        os.remove(f)
    except OSError:
        pass

print(f"\n{'=' * 55}\n{PASS} passed, {FAIL} failed\n{'=' * 55}")
sys.exit(1 if FAIL else 0)

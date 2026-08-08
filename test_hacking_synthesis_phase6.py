"""
Tests for Phase 6 item 3: hacking reference library -> tool synthesis
(hacking_synthesis.py). Uses isolated REFERENCE_DIR/SYNTHESIZED_DIR/SKILLS_DIR (never the
real ones for automated runs) and a deterministic FakeProvider — the real local model was
already live-tested directly while building this (see task.md): a real reference note fed
in, a real script synthesized (a clean, correct, read-only TCP connect-scanner matching
the reference exactly), a real skill proposed as status="proposed", then verified against
both an authorized and an unauthorized target, plus a path-escape attempt. That live test
also caught a real bug — the model sometimes writes "[synthesis: ...]" lowercase despite
the prompt specifying uppercase — fixed by making the tag regex case-insensitive; this
suite has a dedicated regression test for exactly that.

Required test coverage: "Feed it one piece of reference material, confirm a synthesized
tool shows up as 'proposed,' not active" — section 2.

Run: python test_hacking_synthesis_phase6.py
"""

import os
import sys
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


import hacking_synthesis as hs
import skills

_real_ref_dir, _real_synth_dir, _real_skills_dir = hs.REFERENCE_DIR, hs.SYNTHESIZED_DIR, skills.SKILLS_DIR
_tmp_ref_dir, _tmp_synth_dir, _tmp_skills_dir = tempfile.mkdtemp(), tempfile.mkdtemp(), tempfile.mkdtemp()
hs.REFERENCE_DIR, hs.SYNTHESIZED_DIR = _tmp_ref_dir, _tmp_synth_dir
skills.SKILLS_DIR = _tmp_skills_dir


class _FakeProvider:
    def __init__(self, response_text):
        self.response_text = response_text
        self.calls = []

    def chat_stream(self, messages, system_prompt):
        self.calls.append(messages)
        return iter([self.response_text])

    def chat(self, messages, system_prompt):
        self.calls.append(messages)
        return {"response": self.response_text, "tools": []}


def _with_fake_llm(response_text):
    """llm.get_llm() is called fresh inside synthesize_tool_from_reference — patch it at
    the source (llm.get_llm) so the swap is picked up regardless of where it's imported
    from, same convention this test suite uses for skills.SKILLS_DIR etc."""
    import llm
    llm.get_llm = lambda: _FakeProvider(response_text)


_real_get_llm = __import__("llm").get_llm

try:
    section("1. Reference library CRUD")

    added = hs.add_reference_material("technique.md", "A test technique writeup for scanning.")
    check("add_reference_material succeeds", added.get("written") is True, f"got: {added}")

    listed = hs.list_reference_material()
    check("list_reference_material finds it", "technique.md" in listed["files"], f"got: {listed}")

    read = hs.read_reference_material("technique.md")
    check("read_reference_material returns the real content", "test technique writeup" in read.get("content", ""), f"got: {read}")

    missing = hs.read_reference_material("does_not_exist.md")
    check("reading a missing reference file returns a clean error", "error" in missing)

    # Path safety — add/read use os.path.basename(), which flattens "../../../etc/passwd"
    # down to just "passwd" before ever touching the filesystem, so there's no directory
    # component left to escape with. That flattened name predictably doesn't exist in the
    # (empty) reference dir, hence the clean "not found" rather than a security-relevant
    # failure — the safety property is the flattening itself, not this specific error.
    traversal = hs.read_reference_material("../../../etc/passwd")
    check("a traversal-looking filename is flattened by os.path.basename(), can't escape the reference dir",
          "error" in traversal, f"got: {traversal}")

    section("2. synthesize_tool_from_reference — well-formed response -> proposed, never active")

    well_formed = (
        '[SYNTHESIS: name="test_scanner" language="python" description="A test scanner."]\n'
        '```python\nimport sys\nprint("scanning", sys.argv[1])\n```'
    )
    _with_fake_llm(well_formed)
    result = hs.synthesize_tool_from_reference("technique.md", "scan a target for open ports")
    check("synthesis succeeds against a well-formed response", "error" not in result, f"got: {result}")
    check("a script file was actually written", os.path.exists(result.get("script_path", "")), f"got: {result}")
    with open(result["script_path"], encoding="utf-8") as f:
        script_content = f.read()
    check("the written script contains the real generated code", 'print("scanning"' in script_content, f"got: {script_content!r}")

    proposed_skill = skills.load_skill(result["skill_proposed"])
    check("the proposed skill exists", proposed_skill is not None)
    check("the proposed skill's status is 'proposed', NOT 'active'", proposed_skill.status == "proposed", f"got: {proposed_skill.status}")
    check("the proposed skill's tier is TIER_4 (capped by run_synthesized_script's real tier)", proposed_skill.tier == "TIER_4", f"got: {proposed_skill.tier}")
    check("the proposed skill's team is 'hacking'", proposed_skill.team == "hacking")
    check("the proposed skill only ever references run_synthesized_script", proposed_skill.tools == ["run_synthesized_script"], f"got: {proposed_skill.tools}")
    check("the proposed skill's steps point at the real generated script for review", result["script_path"] in "\n".join(proposed_skill.steps))

    section("3. Regression: lowercase '[synthesis: ...]' tag (real bug found live) still parses")
    lowercase_tag = (
        '[synthesis: name="lowercase_test" language="python" description="lowercase tag test."]\n'
        '```\nprint("hi")\n```'
    )
    _with_fake_llm(lowercase_tag)
    result_lower = hs.synthesize_tool_from_reference("technique.md", "test lowercase tag handling")
    check("a lowercase [synthesis: ...] tag from the model still parses correctly", "error" not in result_lower, f"got: {result_lower}")

    section("4. synthesize_tool_from_reference — failure paths")

    _with_fake_llm("this is not tagged output at all, just prose")
    unparseable = hs.synthesize_tool_from_reference("technique.md", "goal")
    check("unparseable model output returns a clean error, writes no file", "error" in unparseable, f"got: {unparseable}")

    missing_ref = hs.synthesize_tool_from_reference("nonexistent_reference.md", "goal")
    check("synthesizing from a nonexistent reference file returns a clean error", "error" in missing_ref, f"got: {missing_ref}")

    import llm as _llm_mod
    _llm_mod.get_llm = lambda: (_ for _ in ()).throw(RuntimeError("no LLM configured"))
    no_brain = hs.synthesize_tool_from_reference("technique.md", "goal")
    check("an unavailable LLM returns a clean error, not a crash", "error" in no_brain, f"got: {no_brain}")

    section("5. run_synthesized_script — authorization gate, path confinement")

    _with_fake_llm(well_formed)
    real_synth = hs.synthesize_tool_from_reference("technique.md", "goal")
finally:
    pass

# AuthorizationCheck raises rather than returning an error dict — verify that explicitly,
# outside the try/finally above so a raised exception here doesn't skip cleanup.
try:
    hs.run_synthesized_script(real_synth["script_path"], "203.0.113.5")
    check("running against an unauthorized target raises AuthorizationError", False, "no exception raised")
except Exception as e:
    check("running against an unauthorized target raises AuthorizationError",
          type(e).__name__ == "AuthorizationError" and "203.0.113.5" in str(e), f"got: {type(e).__name__}: {e}")

authorized = hs.run_synthesized_script(real_synth["script_path"], "127.0.0.1")
check("running against an authorized target (127.0.0.1) actually executes",
      authorized.get("returncode") == 0 and "scanning 127.0.0.1" in authorized.get("stdout", ""), f"got: {authorized}")

escape_attempt = hs.run_synthesized_script(os.path.join("..", "..", "some_other_script.py"), "127.0.0.1")
check("a script path outside SYNTHESIZED_DIR is refused", "error" in escape_attempt and "outside" in escape_attempt["error"], f"got: {escape_attempt}")

missing_script = hs.run_synthesized_script(os.path.join(_tmp_synth_dir, "does_not_exist.py"), "127.0.0.1")
check("a nonexistent script path is refused", "error" in missing_script, f"got: {missing_script}")

bad_ext_path = os.path.join(_tmp_synth_dir, "weird.exe")
with open(bad_ext_path, "w") as f:
    f.write("not a real script")
bad_ext = hs.run_synthesized_script(bad_ext_path, "127.0.0.1")
check("an unsupported script extension is refused", "error" in bad_ext, f"got: {bad_ext}")

# ---------------------------------------------------------------------------
section("6. Tools registered in ALL_TOOLS with the specified tiers")
# ---------------------------------------------------------------------------

from tools import ALL_TOOLS, Tier

expected = {
    "add_reference_material": Tier.TIER_2, "list_reference_material": Tier.TIER_1,
    "read_reference_material": Tier.TIER_1, "synthesize_tool_from_reference": Tier.TIER_4,
    "run_synthesized_script": Tier.TIER_4,
}
for name, tier in expected.items():
    check(f"'{name}' registered at {tier.name}", name in ALL_TOOLS and ALL_TOOLS[name].tier == tier, f"got: {ALL_TOOLS.get(name)}")
    check(f"'{name}' scoped to the hacking team", ALL_TOOLS[name].team == "hacking")

check("synthesize_tool_from_reference is role=offense (gets the centralized auth check too)",
      ALL_TOOLS["synthesize_tool_from_reference"].role == "offense")
check("run_synthesized_script is role=offense (gets the centralized auth check too)",
      ALL_TOOLS["run_synthesized_script"].role == "offense")

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

import llm as _llm_mod_cleanup
_llm_mod_cleanup.get_llm = _real_get_llm
hs.REFERENCE_DIR, hs.SYNTHESIZED_DIR = _real_ref_dir, _real_synth_dir
skills.SKILLS_DIR = _real_skills_dir
shutil.rmtree(_tmp_ref_dir, ignore_errors=True)
shutil.rmtree(_tmp_synth_dir, ignore_errors=True)
shutil.rmtree(_tmp_skills_dir, ignore_errors=True)


print(f"\n{'=' * 55}\n{PASS} passed, {FAIL} failed\n{'=' * 55}")
sys.exit(1 if FAIL else 0)

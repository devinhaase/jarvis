"""
Tests for Phase 8b: self-knowledge document generation (generate_self_knowledge.py) — the
get_self_knowledge tool, drift-check CLI flag, and the pre-commit hook that wires it in.

Run: python test_self_knowledge_phase8b.py
"""

import os
import sys
import re
import shutil
import tempfile
import subprocess

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


import generate_self_knowledge as gsk

# ---------------------------------------------------------------------------
section("generate() content")
# ---------------------------------------------------------------------------

doc = gsk.generate()

check("doc is non-trivially long", len(doc) > 2000, f"got {len(doc)} chars")
check("doc mentions it's auto-generated", "Auto-generated" in doc)
check("doc includes a tool inventory section", "## Tool inventory" in doc)
check("doc includes tier/role breakdown", "Tier & role breakdown" in doc)
check("doc includes security posture section", "Current security posture" in doc)
check("doc includes known scope boundaries", "Known, deliberate scope boundaries" in doc)

from tools import ALL_TOOLS
sample_tool = next(iter(ALL_TOOLS))
check(f"a real registered tool ('{sample_tool}') actually appears in the doc", f"`{sample_tool}`" in doc)

check("doc reports the real total tool count", f"**{len(ALL_TOOLS)} tools registered total.**" in doc,
      f"expected count {len(ALL_TOOLS)}")

# Every tool in ALL_TOOLS should appear somewhere in the generated inventory tables.
missing = [name for name in ALL_TOOLS if f"`{name}`" not in doc]
check("every registered tool appears in the generated inventory", not missing, f"missing: {missing}")

# ---------------------------------------------------------------------------
section("get_self_knowledge() tool entrypoint")
# ---------------------------------------------------------------------------

result = gsk.get_self_knowledge()
check("get_self_knowledge returns a string", isinstance(result, str))
check("get_self_knowledge content matches generate()", result == gsk.generate())  # timestamp granularity is minutes, safe in a fast test

check("'get_self_knowledge' registered in ALL_TOOLS", "get_self_knowledge" in ALL_TOOLS)
from tools import Tier
check("get_self_knowledge is Tier 1 (read-only)", ALL_TOOLS["get_self_knowledge"].tier == Tier.TIER_1)

# ---------------------------------------------------------------------------
section("--check drift detection")
# ---------------------------------------------------------------------------

_real_output = gsk.OUTPUT_PATH
_tmp_dir = tempfile.mkdtemp()
gsk.OUTPUT_PATH = os.path.join(_tmp_dir, "jarvis.md")

try:
    old_argv = sys.argv
    # No file yet at all -> definitely stale.
    sys.argv = ["generate_self_knowledge.py", "--check"]
    try:
        gsk.main()
        check("--check exits nonzero when no file exists yet", False, "main() returned instead of exiting")
    except SystemExit as e:
        check("--check exits nonzero when no file exists yet", e.code == 1, f"got code {e.code}")

    # Now actually write it.
    sys.argv = ["generate_self_knowledge.py"]
    try:
        gsk.main()
    except SystemExit:
        pass
    check("file was written", os.path.exists(gsk.OUTPUT_PATH))

    # Immediately re-checking should now pass (content matches modulo the timestamp line).
    sys.argv = ["generate_self_knowledge.py", "--check"]
    try:
        gsk.main()
        check("--check exits 0 right after a fresh write", False, "main() didn't call sys.exit")
    except SystemExit as e:
        check("--check exits 0 right after a fresh write", e.code == 0, f"got code {e.code}")

    # Corrupt the file to simulate real drift (a tool got added/removed since last commit).
    with open(gsk.OUTPUT_PATH, "a", encoding="utf-8") as f:
        f.write("\nSOME STALE HAND-EDIT THAT SHOULDN'T BE HERE\n")
    sys.argv = ["generate_self_knowledge.py", "--check"]
    try:
        gsk.main()
        check("--check detects real drift", False, "main() didn't call sys.exit")
    except SystemExit as e:
        check("--check detects real drift", e.code == 1, f"got code {e.code}")

    sys.argv = old_argv
finally:
    gsk.OUTPUT_PATH = _real_output
    shutil.rmtree(_tmp_dir, ignore_errors=True)

# Deliberately not asserting the real committed context/self/jarvis.md is drift-free here —
# it legitimately drifts mid-session as tools/tests are added (e.g. the test-file count in
# the doc), and going stale between edits and the next commit is exactly what the
# pre-commit hook below exists to catch and fix automatically, not something this test
# should flag as a failure of the generator itself.

# ---------------------------------------------------------------------------
section("Pre-commit hook")
# ---------------------------------------------------------------------------

check("hooks/pre-commit template exists (tracked in git)", os.path.exists("hooks/pre-commit"))
with open("hooks/pre-commit") as f:
    hook_content = f.read()
check("hook calls generate_self_knowledge.py --check", "generate_self_knowledge.py --check" in hook_content)
check("hook re-stages the file on drift", "git add context/self/jarvis.md" in hook_content)

check(".git/hooks/pre-commit is installed", os.path.exists(".git/hooks/pre-commit"))
if os.path.exists(".git/hooks/pre-commit"):
    with open(".git/hooks/pre-commit") as f:
        installed = f.read()
    check("installed hook matches the tracked template", installed == hook_content)


print(f"\n{'=' * 55}\n{PASS} passed, {FAIL} failed\n{'=' * 55}")
sys.exit(1 if FAIL else 0)

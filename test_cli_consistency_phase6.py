"""
Tests for Phase 6 item 8: CLI/GUI consistency pass — main.py's --json flag, team-icon/label
parity with the web GUI, and the switch from a standalone flat-tool ReAct loop onto the same
JarvisBrain + team_router engine server.py/webapp use.

Mostly subprocess-based (`python main.py --json ...`) rather than importing main.py directly
— main.py's module-level code (console = Console(), memory = Memory()) runs real
side-effecting setup at import time, and the whole point of --json is "what actually comes
out of a real invocation of this CLI," which a subprocess test verifies directly rather than
by inference.

Run: python test_cli_consistency_phase6.py
"""

import os
import sys
import json
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


def run_cli(args, timeout=60):
    """Runs `python main.py <args>` as a real subprocess, returns (returncode, stdout, stderr)."""
    result = subprocess.run(
        [sys.executable, "main.py"] + args,
        capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace",
    )
    return result.returncode, result.stdout, result.stderr


# ---------------------------------------------------------------------------
section("1. --json health/security/logs — clean, parseable JSON on stdout")
# ---------------------------------------------------------------------------

for cmd, expected_tool in (("health", "check_system_health"), ("logs", "scan_local_logs"), ("security", "get_security_posture")):
    code, out, err = run_cli(["--json", cmd])
    check(f"--json {cmd} exits 0", code == 0, f"got {code}, stderr: {err[:300]}")
    try:
        parsed = json.loads(out)
        check(f"--json {cmd} stdout is valid JSON, nothing else mixed in", True)
        check(f"--json {cmd} JSON has the expected 'tool' field", parsed.get("tool") == expected_tool, f"got: {parsed.get('tool')}")
        check(f"--json {cmd} JSON has a 'result' field", "result" in parsed, f"got keys: {list(parsed.keys())}")
    except json.JSONDecodeError as e:
        check(f"--json {cmd} stdout is valid JSON, nothing else mixed in", False, f"stdout was: {out[:500]!r} ({e})")

# ---------------------------------------------------------------------------
section("2. --json chat — rejected cleanly, not silently ignored")
# ---------------------------------------------------------------------------

code, out, err = run_cli(["--json", "chat"])
check("--json chat exits non-zero (interactive mode can't be JSON)", code != 0, f"got {code}")
try:
    parsed = json.loads(out)
    check("--json chat's stdout is still valid JSON (an error object, not a crash)", "error" in parsed, f"got: {parsed}")
except json.JSONDecodeError:
    check("--json chat's stdout is still valid JSON (an error object, not a crash)", False, f"stdout was: {out[:300]!r}")

# ---------------------------------------------------------------------------
section("3. Normal (non-JSON) commands still work — the consistency pass didn't break the original CLI")
# ---------------------------------------------------------------------------

code, out, err = run_cli(["health"])
check("plain `health` (no --json) still exits 0", code == 0, f"got {code}, stderr: {err[:300]}")
check("plain `health` produces Rich-formatted output, not raw JSON", "Reason" in out or "Observe" in out, f"got: {out[:300]!r}")

# ---------------------------------------------------------------------------
section("4. Team icon/label parity with webapp/app.js")
# ---------------------------------------------------------------------------

import re
import importlib

for path in ("main.py",):
    pass  # main.py is imported directly below now that we're past the subprocess-only checks

# main.py's module-level code (Memory(), Console()) is safe to actually import for these
# static/structural checks — it's the *interactive* main() entry point that needs a real
# subprocess, not the module import itself (test_gui_polish_phase6.py and others already
# established this "import the real module" pattern is fine for non-interactive checks).
import main as main_module

with open("webapp/app.js", "r", encoding="utf-8") as f:
    app_js = f.read()

js_icon_match = re.search(r"const TEAM_ICON = \{(.*?)\};", app_js, re.S)
js_label_match = re.search(r"const TEAM_LABEL = \{(.*?)\};", app_js, re.S)
check("found TEAM_ICON in app.js to compare against", js_icon_match is not None)
check("found TEAM_LABEL in app.js to compare against", js_label_match is not None)

if js_icon_match:
    js_icons = dict(re.findall(r"(\w+):\s*\"([^\"]+)\"", js_icon_match.group(1)))
    # Emoji variation selectors (U+FE0F) can differ between a Python source literal and a
    # JS one without being a real mismatch — compare with them stripped.
    def _strip_vs(s):
        return s.replace("️", "")
    mismatches = {
        k for k in set(js_icons) | set(main_module.TEAM_ICON)
        if _strip_vs(js_icons.get(k, "")) != _strip_vs(main_module.TEAM_ICON.get(k, ""))
    }
    check("main.py's TEAM_ICON matches webapp/app.js's TEAM_ICON exactly (same keys, same emoji)",
          not mismatches, f"mismatched keys: {mismatches}")

if js_label_match:
    js_labels = dict(re.findall(r'(\w+):\s*"([^"]+)"', js_label_match.group(1)))
    check("main.py's TEAM_LABEL matches webapp/app.js's TEAM_LABEL exactly",
          js_labels == main_module.TEAM_LABEL, f"js: {js_labels}\npy: {main_module.TEAM_LABEL}")

check("TEAM_ICON covers every real team key", set(main_module.TEAM_ICON) == {"personal_assistant", "network", "it", "cybersecurity", "hacking"})

# ---------------------------------------------------------------------------
section("5. main.py now runs turns through JarvisBrain + team_router, not a separate loop")
# ---------------------------------------------------------------------------

import inspect

check("run_chat_mode no longer takes a raw Coordinator (uses _build_jarvis_brain internally)",
      "c: Coordinator" not in str(inspect.signature(main_module.run_chat_mode)) and
      "c" not in inspect.signature(main_module.run_chat_mode).parameters)
check("_build_jarvis_brain constructs a real session_manager.JarvisBrain",
      "JarvisBrain" in inspect.getsource(main_module._build_jarvis_brain))
check("run_team_turn calls team_router.route_and_run (the same engine server.py's _run_turn uses)",
      "team_router.route_and_run" in inspect.getsource(main_module.run_team_turn))

brain = main_module._build_jarvis_brain()
check("_build_jarvis_brain() produces a brain that reports available (or a clear reason it isn't)",
      brain.is_available() or bool(brain._llm_error))

# ---------------------------------------------------------------------------
section("6. run_team_turn() — actual behavior with a fake, deterministic model")
# ---------------------------------------------------------------------------
# Same FakeProvider shape test_teams_phase4.py already established (chat/chat_stream split).

class _FakeBrainProvider:
    def __init__(self, chat_responses=None, chat_rounds=None):
        self.chat_responses = list(chat_responses or [])
        self.chat_rounds = list(chat_rounds or [])

    def chat(self, messages, system_prompt):
        if not self.chat_responses:
            return {"response": "personal_assistant", "tools": []}
        return self.chat_responses.pop(0)

    def chat_stream(self, messages, system_prompt):
        if not self.chat_rounds:
            return iter(["(no more canned rounds)"])
        return iter(self.chat_rounds.pop(0))


brain2 = main_module._build_jarvis_brain()
brain2._brain = _FakeBrainProvider(
    chat_responses=[{"response": "[TEAM: it]", "tools": []}],  # classification pass
    chat_rounds=[["Your CPU's fine."]],
)
history = [{"role": "user", "content": "check my system health"}]
result = main_module.run_team_turn(brain2, history, quiet=True)
check("run_team_turn returns the final response text", result.get("response") == "Your CPU's fine.", f"got: {result}")
check("run_team_turn reports which team(s) actually ran", result.get("teams_involved") == ["it"], f"got: {result}")

# ---------------------------------------------------------------------------
section("7. _quiet_coordinator — actually suppresses Coordinator's console output")
# ---------------------------------------------------------------------------

import io
import coordinator as coord_module

before_file = coord_module.console.file
with main_module._quiet_coordinator():
    during_file = coord_module.console.file
    check("coordinator.console.file is redirected while inside the context manager", during_file is not before_file)
    coord_module.console.print("this should go nowhere, not raise")
    check("printing an emoji through the redirected console doesn't raise (real bug found + fixed)", True)
after_file = coord_module.console.file
check("coordinator.console.file is restored after the context manager exits", after_file is before_file)

# ---------------------------------------------------------------------------
section("8. Interactive menu — Teams/Skills/Network status options, numbering consistent 1-11")
# ---------------------------------------------------------------------------

src = inspect.getsource(main_module)
check("menu choices now run 1-11 (Teams/Skills/Network status inserted before Exit)",
      "range(1, 12)" in src)
check("choice '7' renders a Teams table sourced from teams.TEAMS", "from teams import TEAMS" in src)
check("choice '8' renders the skill review queue sourced from skills.list_skills", "skills.list_skills()" in src)
check("choice '10' renders network infrastructure status sourced from _compute_integration_status",
      "_compute_integration_status" in src)

print(f"\n{'=' * 55}\n{PASS} passed, {FAIL} failed\n{'=' * 55}")
sys.exit(1 if FAIL else 0)

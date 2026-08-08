"""
Tests for Phase 7: start_jarvis_server.bat — sanity checks that the startup script's
referenced paths are real (the actual "does it launch a working server" check was done by
hand, once, since spawning a real second server process from inside a test suite would be
disruptive — see the session notes / summary for that live verification).

Run: python test_startup_script_phase7.py
"""

import os
import sys
import re

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


section("1. start_jarvis_server.bat exists and references real paths")

BAT_PATH = "start_jarvis_server.bat"
check("the batch file exists at the project root", os.path.exists(BAT_PATH))

if os.path.exists(BAT_PATH):
    with open(BAT_PATH, 'r') as f:
        content = f.read()

    check("changes directory to the actual project folder", "cd /d" in content)

    project_dir_match = re.search(r'cd /d "([^"]+)"', content)
    check("the referenced project directory actually exists",
          project_dir_match and os.path.isdir(project_dir_match.group(1)),
          f"got: {project_dir_match.group(1) if project_dir_match else None}")
    if project_dir_match:
        check("server.py actually exists inside that directory",
              os.path.exists(os.path.join(project_dir_match.group(1), "server.py")))

    python_match = re.search(r'"([^"]+python\.exe)"', content)
    check("references a real python.exe that actually exists on disk",
          python_match and os.path.exists(python_match.group(1)),
          f"got: {python_match.group(1) if python_match else None}")

    check("actually invokes server.py, not some other script", "server.py" in content)

section("2. Installed into the real Windows Startup folder")

startup_dir = os.path.join(
    os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", "Startup"
)
startup_copy = os.path.join(startup_dir, "start_jarvis_server.bat")
check("a copy exists in the user's Startup folder (runs automatically at login)",
      os.path.exists(startup_copy), f"expected at: {startup_copy}")

if os.path.exists(startup_copy) and os.path.exists(BAT_PATH):
    with open(startup_copy, 'r') as f:
        startup_content = f.read()
    with open(BAT_PATH, 'r') as f:
        source_content = f.read()
    check("the Startup-folder copy matches the project's source script (not stale)",
          startup_content == source_content)

print(f"\n{'=' * 55}\n{PASS} passed, {FAIL} failed\n{'=' * 55}")
sys.exit(1 if FAIL else 0)

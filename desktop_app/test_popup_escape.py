"""
test_popup_escape.py — one-off verification for Tier 3, not part of the shipped app.
Launches the same window setup window.py's main() uses, triggers a window.open() call (the
same call shape an OAuth pop-up uses) via evaluate_js, and checks two things: (1) the
embedded window's own URL never changed — it should still be Jarvis's UI, not the popup
target; (2) a real browser process actually appeared to handle it.
"""

import subprocess
import sys
import threading
import time

import webview
from window import APP_NAME, APP_URL, DEFAULT_WIDTH, DEFAULT_HEIGHT, MIN_WIDTH, MIN_HEIGHT

webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True

TEST_URL = "https://accounts.google.com/o/oauth2/v2/auth?test=pywebview-popup-check"

result_holder = {}
done = threading.Event()


def _browser_pids():
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq brave.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True,
    ).stdout
    return set(line.split(",")[1].strip('"') for line in out.splitlines() if line.strip() and "INFO:" not in line)


def _on_loaded():
    before_pids = _browser_pids()
    window.evaluate_js(f"window.open('{TEST_URL}', '_blank');")
    time.sleep(2.5)  # give the OS a moment to actually spawn/focus the browser
    after_pids = _browser_pids()
    result_holder["embedded_url_unchanged"] = window.get_current_url() == APP_URL
    result_holder["new_browser_pid_appeared"] = len(after_pids - before_pids) > 0
    result_holder["before_pids"] = before_pids
    result_holder["after_pids"] = after_pids
    done.set()


window = webview.create_window(
    APP_NAME, APP_URL,
    width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT,
    min_size=(MIN_WIDTH, MIN_HEIGHT),
)
window.events.loaded += _on_loaded


def _runner():
    if not done.wait(timeout=20):
        result_holder["timeout"] = True
    print(f"TIER3_RESULT: {result_holder}")
    sys.stdout.flush()
    window.destroy()


threading.Thread(target=_runner, daemon=True).start()
webview.start(gui="edgechromium")

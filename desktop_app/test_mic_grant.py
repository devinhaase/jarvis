"""
test_mic_grant.py — one-off verification for Tier 2, not part of the shipped app. Launches
the exact same window setup window.py's main() uses (including _wire_mic_permission), then
calls getUserMedia({audio: true}) directly via evaluate_js and reports whether the browser
resolved (permission actually granted, not just "no crash") or rejected (denied/no device).

This tests the permission-grant code path itself, not "can a human hear their own voice" —
that part still needs a real person at the real app (see task.md's Tier 2 verify step).
What this DOES prove or disprove conclusively: whether _wire_mic_permission's hook into
CoreWebView2.PermissionRequested actually intercepts and grants the request, as opposed to
it silently falling through to WebView2's default (which denies some permission kinds
outright depending on runtime/group policy, per Tier 2's own docstring in window.py).
"""

import sys
import threading

import webview
from window import APP_NAME, APP_URL, DEFAULT_WIDTH, DEFAULT_HEIGHT, MIN_WIDTH, MIN_HEIGHT, _wire_mic_permission

webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True

PROBE_JS = """
new Promise((resolve) => {
  navigator.mediaDevices.getUserMedia({ audio: true })
    .then((stream) => {
      stream.getTracks().forEach((t) => t.stop());
      resolve("GRANTED");
    })
    .catch((e) => resolve("DENIED: " + e.name + " - " + e.message));
});
"""

result_holder = {}
done = threading.Event()


def _on_loaded():
    _wire_mic_permission(window)

    def _cb(value):
        result_holder["value"] = value
        done.set()

    window.evaluate_js(PROBE_JS, callback=_cb)


window = webview.create_window(
    APP_NAME, APP_URL,
    width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT,
    min_size=(MIN_WIDTH, MIN_HEIGHT),
)
window.events.loaded += _on_loaded


def _runner():
    if not done.wait(timeout=20):
        result_holder["value"] = "TIMEOUT — evaluate_js callback never fired"
    print(f"MIC_PROBE_RESULT: {result_holder.get('value')}")
    sys.stdout.flush()
    window.destroy()


threading.Thread(target=_runner, daemon=True).start()
webview.start(gui="edgechromium")

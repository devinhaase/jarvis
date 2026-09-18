"""
window.py — the desktop app shell. Tier 1 of the native-app work (see task.md's entry):
a chrome-less native window over Jarvis's own local web UI. Runs in its own dedicated venv
(desktop_app/.venv) — pywebview/pythonnet are GUI dependencies with nothing to do with the
agent itself, no reason to install them into the server's own environment.

Almost nothing about Jarvis moves. The server (server.py), the WebSocket, the voice
pipeline, the model calls — all of it keeps running exactly as it does today, exactly as
it would in a normal browser tab. This file is only the window around that tab: on Windows
that's pywebview's "edgechromium" backend, a thin wrapper over the same WebView2 engine
real Edge/Chrome use, not a second browser or a reimplementation of anything.

Tiers, in order (see task.md for the full writeup of each):
  1. This file's bare window — DONE.
  2. Microphone permission — WebView2 has no default "may this page use the mic" grant the
     way a normal browser tab does; pywebview doesn't handle it either (confirmed by reading
     its actual source, not assumed) — needs a direct hook into the underlying
     CoreWebView2.PermissionRequested event. DONE.
  3. OAuth/external links escaping to the real browser — pywebview's edgechromium backend
     already wires window.open()/target=_blank through window.settings
     (OPEN_EXTERNAL_LINKS_IN_BROWSER), confirmed from its own source. DONE.
  4. A splash screen that starts the server if it's asleep and waits for it — this tier.
  5. Bundling this into something double-click-launchable with an icon.
"""

import os
import subprocess
import sys
import threading
import time

import requests
import webview

# ---------------------------------------------------------------------------
# Config — the few things that make this Jarvis's window and not a generic one.
# ---------------------------------------------------------------------------

APP_NAME = "Jarvis"
APP_URL = "http://localhost:8765/"
HEALTH_URL = "http://localhost:8765/health"
VERSION_URL = "http://localhost:8765/native-shell-version"

# Part 3 (native-app update flow) — this shell's own version. Bump this alongside
# server.py's DESKTOP_MIN_VERSION whenever shipping a change to *this file* (not a web/
# dashboard-only change, which every already-installed copy of this window already picks up
# on its next launch with zero rebuild needed — see task.md's Part 3 entry for the
# distinction this whole mechanism exists to draw).
APP_VERSION = "1.0.0"

# Comfortable default for a chat+dashboard UI; minimum keeps the sidebar/composer usable
# rather than letting someone resize it into something broken.
DEFAULT_WIDTH, DEFAULT_HEIGHT = 1280, 860
MIN_WIDTH, MIN_HEIGHT = 720, 480

# ---------------------------------------------------------------------------
# Tier 4 config — how to start the server if it's asleep, and how long to wait.
#
# The only genuinely machine-specific values in this whole file: this is exactly the same
# command start_jarvis_server.bat (in the project root, copied to the Windows Startup
# folder) already runs. Duplicated here rather than shelling out to the .bat itself so this
# still works even if that file moves/changes — and so the desktop app doesn't depend on a
# Startup-folder script existing at all. If this ever gets copied to another machine (see
# task.md's distribution-scope note), these three lines are the only thing that needs
# updating for it to still know how to wake its own server there.
# ---------------------------------------------------------------------------

# Updated after the OneDrive sync incident (Sept 2026) moved the live deployment to its
# own git clone outside OneDrive, with its own dedicated venv — same reasoning
# start_jarvis_server.bat's own comment gives for using a project-specific venv over PATH
# resolution at boot time.
PROJECT_ROOT = r"C:\Users\devin\dev\jarvis"
SERVER_PYTHON = r"C:\Users\devin\dev\jarvis\.venv\Scripts\python.exe"
SERVER_SCRIPT = "server.py"

SERVER_START_TIMEOUT_S = 60   # how long to wait for a cold start before giving up
POLL_INTERVAL_S = 1           # matches the spec's "poll once a second"
HEALTH_CHECK_TIMEOUT_S = 1.5  # a single health-check request's own timeout — short, so a hung request never eats into the poll budget

# Tier 3: pywebview's edgechromium backend already implements
# on_new_window_request(sender, args) — window.open()/target=_blank/OAuth pop-ups — and
# reads this setting to decide whether to hand the URL to the system browser (True) or try
# to load it inside this same chrome-less window (False, the default). Jarvis's own OAuth
# flows (Google sign-in for Gmail/Calendar/Drive) need the real browser: the provider's
# consent screen won't even load for an app-identified WebView2 in some configurations, and
# even when it does, there's no chrome to see the address bar / confirm you're on Google's
# real domain. This must be set before webview.start() — pywebview reads it once at window
# creation, not on every navigation.
webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True

# ---------------------------------------------------------------------------
# Tier 2 — microphone permission.
#
# A normal browser tab shows its own "example.com wants to use your microphone" prompt the
# first time a page calls getUserMedia({audio: true}), and remembers the answer. WebView2
# has the exact same concept (CoreWebView2.PermissionRequested), but pywebview's
# edgechromium backend never subscribes to it — confirmed by reading its actual installed
# source (desktop_app/.venv/Lib/site-packages/webview/platforms/edgechromium.py), not
# assumed. Left alone, an unhandled PermissionRequested event falls back to WebView2's own
# default UI prompt for some permission kinds, but denies others outright depending on
# runtime/policy — not something to leave to chance for the one thing this app most needs
# to just work. So: reach into the live CoreWebView2 object for this window and answer the
# mic (and camera, for the same getUserMedia call shape) requests ourselves, deterministically.
#
# No Info.plist/TCC bundle-identity dance here (that's the macOS-specific half of Tier 2)
# — Windows attributes the permission to whichever process actually calls getUserMedia
# through WebView2, which is this app's own .exe once Tier 5 bundles it, no separate
# "usage description string" mechanism to get right or wrong.
# ---------------------------------------------------------------------------

_mic_wired_for = set()  # window.uid values already wired — events.loaded can refire on navigation; don't double-subscribe


def _wire_mic_permission(window):
    if sys.platform != "win32" or window.uid in _mic_wired_for:
        return

    # Deferred import, deliberately not at module level: `Microsoft.Web.WebView2.Core`
    # only becomes importable once pywebview's edgechromium.py has actually run its own
    # `clr.AddReference('Microsoft.Web.WebView2.Core.dll')` — which happens lazily inside
    # webview.start(gui=...), not from `import webview` alone. Importing this at the top of
    # the file (tried first, failed with `ModuleNotFoundError: No module named 'Microsoft'`)
    # runs before that reference exists. events.loaded only ever fires after start(), so by
    # the time this function runs the assembly is guaranteed already loaded.
    from Microsoft.Web.WebView2.Core import CoreWebView2PermissionKind, CoreWebView2PermissionState
    from webview.platforms.winforms import BrowserView
    from System import Action

    def _grant_media_permission(sender, args):
        kind = args.PermissionKind
        if kind in (CoreWebView2PermissionKind.Microphone, CoreWebView2PermissionKind.Camera):
            args.State = CoreWebView2PermissionState.Allow

    # BrowserView.instances is pywebview's own internal registry (winforms.py), keyed by the
    # same uid every public Window exposes — this is the one path from "the Window object
    # main() got back" down to the live CoreWebView2 COM object underneath it. By the time
    # events.loaded fires, CoreWebView2InitializationCompleted has already fired too (the
    # page couldn't have loaded otherwise), so .CoreWebView2 is guaranteed non-null here...
    # except that pywebview's events.loaded fires from its own dispatch thread, not
    # WinForms' UI thread — a real, hit-on-first-try error, not a guess:
    # "System.InvalidOperationException: CoreWebView2 can only be accessed from the UI
    # thread." WebView2's COM object is apartment-threaded like any other WinForms control;
    # touching it off-thread throws instead of silently working. Fix is the standard
    # WinForms one — marshal the actual access back onto the control's own thread via
    # .Invoke(), the same pattern edgechromium.py itself uses internally for its own
    # cross-thread calls (TaskScheduler.FromCurrentSynchronizationContext()).
    browser_form = BrowserView.instances.get(window.uid)
    if browser_form is None:
        return
    webview2_control = browser_form.browser.webview

    def _attach_on_ui_thread():
        webview2_control.CoreWebView2.PermissionRequested += _grant_media_permission
        _mic_wired_for.add(window.uid)

    webview2_control.Invoke(Action(_attach_on_ui_thread))


# ---------------------------------------------------------------------------
# Part 3 — "you're out of date" check. Only ever fires for a native-shell change (this
# file) — a web/dashboard-only change needs nothing here, every window already shows the
# latest UI on its next load regardless of this file's own version. See server.py's
# DESKTOP_MIN_VERSION comment for the other half of this mechanism.
# ---------------------------------------------------------------------------

def _parse_version(v: str) -> tuple:
    try:
        return tuple(int(p) for p in v.strip().split("."))
    except (ValueError, AttributeError):
        return (0,)  # unparsable -> treat as "very old", never as "definitely current"


def _check_version(window):
    try:
        resp = requests.get(VERSION_URL, timeout=3)
        minimum = resp.json().get("desktop_min_version")
    except (requests.RequestException, ValueError):
        return  # server unreachable or malformed response — not worth a banner over, just skip silently until the next check
    if minimum is None or _parse_version(APP_VERSION) >= _parse_version(minimum):
        return

    # Injected via JS rather than a native pywebview element — this window has no chrome of
    # its own to add a banner to outside the page content, and unlike the splash (its own
    # static HTML string), the real page is Jarvis's own live UI. Idempotent: checks for
    # the banner's own id first, so a second events.loaded firing (e.g. splash -> real page)
    # never stacks a duplicate.
    banner_js = f"""
    (function() {{
      if (document.getElementById('jarvis-desktop-update-banner')) return;
      var b = document.createElement('div');
      b.id = 'jarvis-desktop-update-banner';
      b.textContent = 'A new desktop build is ready — ask to have it rebuilt and reinstalled.';
      b.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:99999;background:#0a84ff;'
        + 'color:#fff;font:13px -apple-system,Segoe UI,sans-serif;text-align:center;padding:6px;';
      document.body.appendChild(b);
    }})();
    """
    try:
        window.evaluate_js(banner_js)
    except Exception:
        pass  # window may be mid-transition; the next loaded event tries again


# ---------------------------------------------------------------------------
# Tier 4 — a splash screen that wakes a sleeping server.
#
# start_jarvis_server.bat runs at login (it's in the Startup folder), but that only covers
# "the computer just booted." Close its console window, or the server crash for any reason,
# and it stays down until next login — a cold click on this app would otherwise hit
# ERR_CONNECTION_REFUSED and show WebView2's own ugly error page. Instead: check first,
# and if it's down, show something in Jarvis's own colors while we start it and wait.
# ---------------------------------------------------------------------------

# Matches manifest.json's PWA chrome colors (background_color/theme_color) rather than
# re-deriving from style.css's CSS variables — this is the same "app chrome," just rendered
# before any of Jarvis's own CSS has loaded (there's nothing to load yet).
SPLASH_HTML = """
<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  html, body { margin: 0; height: 100%; background: #1a1a1e; overflow: hidden; }
  body {
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    color: #ececee;
  }
  .brand { font-weight: 700; letter-spacing: 0.14em; font-size: 22px; color: #ececee; }
  .dot {
    width: 10px; height: 10px; border-radius: 50%; background: #0a84ff; margin-top: 22px;
    animation: pulse 1.1s infinite ease-in-out;
  }
  @keyframes pulse { 0%, 100% { transform: scale(0.7); opacity: 0.5; } 50% { transform: scale(1.15); opacity: 1; } }
  #status { margin-top: 16px; font-size: 13px; color: #96969e; min-height: 18px; }
  #retryBtn {
    display: none; margin-top: 18px; background: #0a84ff; color: #fff; border: none;
    border-radius: 8px; padding: 8px 18px; font-size: 13px; cursor: pointer;
  }
</style></head>
<body>
  <div class="brand">JARVIS</div>
  <div class="dot"></div>
  <div id="status">Starting…</div>
  <button id="retryBtn" onclick="location.reload()">Retry</button>
  <script>
    // window.py drives this via evaluate_js — see _wait_for_server_and_load().
    window.setSplashStatus = function(text, showRetry) {
      document.getElementById('status').textContent = text;
      document.getElementById('retryBtn').style.display = showRetry ? 'inline-block' : 'none';
    };
  </script>
</body></html>
"""


def _server_is_up() -> bool:
    try:
        resp = requests.get(HEALTH_URL, timeout=HEALTH_CHECK_TIMEOUT_S)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def _start_server_process():
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "server_autostart.log")
    # CREATE_NO_WINDOW: this is an app launching its own backend, not a person running a
    # script — a console window flashing open would break the "feels like an app" goal Tier
    # 5 is building toward. Output still goes somewhere (this log), it's just not a visible
    # window — same reasoning start_jarvis_server.bat's own comment gives for using a fixed
    # python.exe path rather than PATH resolution: don't leave anything to an environment
    # that might differ from what's actually been tested.
    with open(log_path, "a", encoding="utf-8") as log_file:
        log_file.write(f"\n--- autostart attempt {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        log_file.flush()
        subprocess.Popen(
            [SERVER_PYTHON, SERVER_SCRIPT],
            cwd=PROJECT_ROOT,
            stdout=log_file, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )


def _set_splash_status(window, text, show_retry=False):
    try:
        window.evaluate_js(f"window.setSplashStatus({text!r}, {str(show_retry).lower()})")
    except Exception:
        pass  # window may already be mid-transition to the real UI — never let a status update crash the wake sequence


def _wait_for_server_and_load(window):
    """Runs on a background thread, started right after create_window() returns (not tied
    to events.loaded — that event fires again once load_url() below succeeds and swaps in
    the real UI, which would re-trigger this function and loop forever if it were the
    trigger instead of a one-shot call from main()). events.shown gates the actual work so
    this never touches the window before pywebview's native side genuinely exists yet.
    Starts the server if it's not already coming up on its own, polls once a second, and
    swaps the splash out for the real UI (window.load_url) the instant it answers — or,
    after SERVER_START_TIMEOUT_S, shows a clear failure state instead of hanging forever."""
    if not window.events.shown.wait(15):
        return  # window never actually showed — nothing sensible to do here, let it fail visibly some other way
    if _server_is_up():
        window.load_url(APP_URL)
        return

    _set_splash_status(window, "Starting Jarvis…")
    try:
        _start_server_process()
    except Exception as e:
        _set_splash_status(window, f"Couldn't start the server: {e}", show_retry=True)
        return

    deadline = time.monotonic() + SERVER_START_TIMEOUT_S
    while time.monotonic() < deadline:
        if _server_is_up():
            window.load_url(APP_URL)
            return
        remaining = int(deadline - time.monotonic())
        _set_splash_status(window, f"Waiting for server… ({max(remaining, 0)}s)")
        time.sleep(POLL_INTERVAL_S)

    _set_splash_status(
        window,
        "Server didn't come up in time. Check desktop_app/logs/server_autostart.log.",
        show_retry=True,
    )


def main():
    # Checked here, before create_window, rather than always starting on the splash and
    # skipping straight through — the fast path (server already up, by far the common case
    # once Tier 5's app has been used a few times) should show Jarvis immediately, not flash
    # a splash for one poll cycle first.
    already_up = _server_is_up()

    window = webview.create_window(
        APP_NAME,
        APP_URL if already_up else None,
        html=None if already_up else SPLASH_HTML,
        width=DEFAULT_WIDTH,
        height=DEFAULT_HEIGHT,
        min_size=(MIN_WIDTH, MIN_HEIGHT),
        text_select=True,
        background_color="#1a1a1e",  # matches manifest.json's PWA background_color — avoids a white flash before either the splash or Jarvis's own (dark-by-default) CSS has painted anything
    )
    window.events.loaded += lambda: _wire_mic_permission(window)
    window.events.loaded += lambda: threading.Thread(target=_check_version, args=(window,), daemon=True).start()
    if not already_up:
        threading.Thread(target=_wait_for_server_and_load, args=(window,), daemon=True).start()

    # gui="edgechromium" is the only real Windows backend (the legacy "mshtml" renderer
    # can't run a modern SPA at all) — named explicitly rather than left to pywebview's
    # auto-detection so a missing WebView2 runtime fails with a clear pywebview error
    # instead of silently falling back to something that can't render this UI.
    webview.start(gui="edgechromium", debug="--debug" in sys.argv)


if __name__ == "__main__":
    main()

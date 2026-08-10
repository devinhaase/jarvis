# Jarvis desktop app (Windows)

A chrome-less native window over Jarvis's own local web UI (`http://localhost:8765/`),
launched from a Desktop/Start Menu shortcut with its own icon, that starts the server if
it's asleep and just works when you click it. See `task.md`'s entries for the full
tier-by-tier build writeup and every real bug hit along the way. This file is the quick
reference: what's here, how to rebuild, how to troubleshoot.

**The brain travels now (Phase 8).** This app loads `http://localhost:8765/` — always the
same machine `server.py` runs on, wherever that machine physically is. Nothing about this
app changes when the laptop travels; it never depended on being on any particular network,
only on being co-located with the server process itself. The Android app's "main computer"
Wake-on-LAN/local-network story is the one that actually shifted with the travel topology
— see `android_app/README.md`'s own note on that.

## Files

| File | What it does |
|---|---|
| `window.py` | The app itself — Tiers 1–4 (window, mic permission, OAuth/link handling, server-wake splash). This is what actually runs. |
| `build_icon.py` | Regenerates `icon.ico` from `../webapp/icon.svg`. Run manually, only when the icon design changes. |
| `build.py` | Creates/updates the Desktop + Start Menu shortcuts. Run after any change to `window.py`, or after moving this folder. |
| `icon.ico` | Checked-in build artifact, not hand-edited. |
| `requirements.txt` | Frozen dependencies for `.venv` (pywebview, pythonnet, requests, Pillow). |
| `logs/server_autostart.log` | Output from the server whenever `window.py` had to start it itself (appended, not truncated). |
| `test_mic_grant.py`, `test_popup_escape.py` | One-off diagnostics used to verify Tiers 2/3, not part of the app. Safe to rerun any time something in those tiers seems broken. |

## Part 3 — knowing when this app itself needs rebuilding

Most changes to Jarvis (dashboard tweaks, new chat features, prompt/personality changes —
anything in `webapp/`) are served live from `server.py` and need **zero** action here: this
app just loads the page fresh every launch, same as a browser tab. Nothing below applies to
those changes.

This app only needs an actual rebuild+reinstall when `window.py` itself changes — the native
shell code: window/splash behavior, mic permission wiring, the server-autostart logic, the
version check described below, etc.

To make sure a stale native shell doesn't go unnoticed after one of those changes,
`window.py` checks `server.py`'s `GET /native-shell-version` endpoint once per launch
(`_check_version()`, called off `window.events.loaded` on a background thread so it never
blocks the window from opening) and compares the server's `desktop_min_version` against this
build's own `APP_VERSION` constant. If the server reports a newer minimum than this build,
it injects a small banner into the loaded page (`#jarvis-desktop-update-banner`, idempotent —
won't double-inject if `_check_version` somehow runs twice) reading "A new desktop build is
ready — ask to have it rebuilt and reinstalled." If they match, nothing is injected — no
banner, no console noise.

**When bumping `window.py` in a way that matters**, bump `APP_VERSION` in `window.py` and
`DESKTOP_MIN_VERSION` in `server.py` together, then rebuild+redistribute this app — that's
what makes any copy still running the old shell show the banner until it's updated.

Live-verified both directions: temporarily set `DESKTOP_MIN_VERSION = "2.0.0"` in `server.py`
with this app still on `APP_VERSION = "1.0.0"`, restarted the server, relaunched the app, and
confirmed (via Windows UI Automation, since automated screenshots don't reflect the real
interactive desktop — see the project's own verification notes) the banner text appeared
exactly as written above. Reverted `DESKTOP_MIN_VERSION` back to `"1.0.0"`, restarted again,
and confirmed no banner appears on a normal, up-to-date launch.

## First-time setup / rebuilding on a new machine

```
cd desktop_app
uv venv .venv
uv pip install --python .venv -r requirements.txt
python build_icon.py   # only if icon.ico is missing or the icon changed
python build.py
```

`build.py` is safe to rerun any time — it overwrites the shortcuts in place. This is also
the entire **update flow**: change `window.py`, rerun `build.py`, done. No uninstall step.

## The one thing that does NOT move with this folder

`window.py`'s `PROJECT_ROOT` and `SERVER_PYTHON` constants (used only to start the server if
it's asleep — Tier 4) are hardcoded to this machine's actual paths. Everything else in this
folder is computed relative to its own location and travels fine if you copy `desktop_app/`
elsewhere. If you ever move this to another machine, update those two constants (and rerun
`build.py`) — the app will still open fine without that fix, it just won't be able to
autostart a sleeping server there.

## Known, current limitation (not a bug in this code)

Live-testing Tier 2 turned up a real finding worth knowing about: this machine's actual
Realtek microphone endpoint currently reports `DEVPKEY_Device_IsPresent = False` to Windows
itself (`Get-PnpDevice` shows it as `Status: Unknown`) — independent of anything in
`window.py`. The mic-permission code was verified correct as far as is possible without a
present device: `getUserMedia` reaches Chromium's device-enumeration stage cleanly (no
`NotAllowedError`/permission block at all — the `PermissionRequested` hook is confirmed
working) and only then fails with `NotFoundError` because Windows has no active capture
device to hand over right now. If voice doesn't work the first time you try it in the real
app, check Windows Sound settings for the actual input device before assuming the app is
broken.

## Troubleshooting map

- **Mic prompt/audio dead, `NotAllowedError`** → the `PermissionRequested` hook in
  `_wire_mic_permission()` isn't attaching. Rerun `test_mic_grant.py` for a direct check.
- **Mic dead with `NotFoundError`** → not a code issue, see the limitation above — check
  Windows' actual default recording device.
- **Sign-in does nothing** → `OPEN_EXTERNAL_LINKS_IN_BROWSER` not taking effect; rerun
  `test_popup_escape.py`.
- **Blank/error window on launch** → check `logs/server_autostart.log` — either the server
  failed to start, or `SERVER_START_TIMEOUT_S` (60s) wasn't enough on a slow machine.
- **Generic/blank icon on the shortcut** → `icon.ico` missing or the shortcut's
  `IconLocation` is stale; rerun `build_icon.py` then `build.py`.
- **SmartScreen "unknown publisher" warning** → expected for an unsigned local build (see
  Part 1's interview answer in `task.md`) — "More info" → "Run anyway", once per fresh
  build/machine. In practice this only fires for files carrying an internet-zone
  Mark-of-the-Web (e.g. downloaded as a zip) — a build done locally by `build.py`, as above,
  didn't trigger it at all in testing.

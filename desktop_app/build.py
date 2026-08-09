"""
build.py — Tier 5: assembles the "double-click launchable, has its own icon" desktop app.

Windows doesn't have a .app-bundle concept to hand-roll the way macOS does (no Info.plist,
no .icns, no code-signing-for-TCC-identity dance) — the actual deliverable here is simpler:
a Start Menu (and Desktop) shortcut that points pythonw.exe at window.py with Jarvis's icon
attached to the shortcut itself. pythonw.exe (not python.exe) is what makes double-clicking
it open a native window with no console flashing open behind it — the one Windows-specific
detail that actually matters for "feels like an app, not a script."

Unsigned, on purpose (Devin's own call, Part 1's interview): SmartScreen may show an
"unknown publisher" warning the first time this runs after a fresh build — click through it
once. Real code signing needs a certificate (paid, or a self-signed one you'd have to
manually trust on this machine) and wasn't asked for. If that ever changes, the target here
stays the same; only an extra `signtool sign ...` step gets added after this script runs.

Kept portable on purpose (Devin: "might move it / share it later" — see task.md): every path
this script writes into the shortcut is computed from THIS_DIR at run time, not hardcoded.
Moving the whole `desktop_app/` folder (and rerunning this script, which is cheap and fast)
to another location or machine regenerates a correct shortcut there — the one thing that
does NOT travel automatically is window.py's own PROJECT_ROOT/SERVER_PYTHON constants (the
Tier 4 server-autostart command), which are genuinely this-machine-specific and documented
as exactly that in window.py itself.

Rerun this exact script any time the desktop shell itself changes (Part 3's "Desktop update
flow") — it overwrites the shortcuts in place, nothing to uninstall/reinstall first.
"""

import os
import subprocess
import sys
import tempfile
import winreg

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_PYTHONW = os.path.join(THIS_DIR, ".venv", "Scripts", "pythonw.exe")
WINDOW_SCRIPT = os.path.join(THIS_DIR, "window.py")
ICON_PATH = os.path.join(THIS_DIR, "icon.ico")

START_MENU_DIR = os.path.join(
    os.environ["APPDATA"], "Microsoft", "Windows", "Start Menu", "Programs"
)

SHORTCUT_NAME = "Jarvis.lnk"

# WScript.Shell's COM shortcut API, driven from PowerShell rather than adding pywin32 as a
# venv dependency just for this one build-time step — every Windows install already has
# both PowerShell and this COM object, no extra install required.
#
# $ErrorActionPreference = "Stop" matters here, not just style: without it, a real failure
# in $shortcut.Save() (hit on first try below — DirectoryNotFoundException) prints an error
# but the script still exits 0, so Python's `check=True` never catches it and build.py
# happily reports "Shortcut written" for a file that was never actually created. Found this
# live, not guessed at — the fix is both this line AND build.py verifying the .lnk exists
# afterward (belt and suspenders; don't trust exit code 0 alone for a script this loose).
_MAKE_SHORTCUT_PS = r"""
param($LinkPath, $TargetPath, $Arguments, $WorkingDir, $IconPath)
$ErrorActionPreference = "Stop"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($LinkPath)
$shortcut.TargetPath = $TargetPath
$shortcut.Arguments = $Arguments
$shortcut.WorkingDirectory = $WorkingDir
$shortcut.IconLocation = $IconPath
$shortcut.Description = "Jarvis"
$shortcut.Save()
"""


def _real_desktop_dir() -> str:
    # NOT just USERPROFILE\Desktop — hit this live: OneDrive's "Known Folder Move" feature
    # (active on this machine — the whole project already lives under
    # ...\OneDrive\Desktop\AI Agent\jarvis, which was the actual tell) redirects the real
    # Desktop folder elsewhere, and USERPROFILE\Desktop plain doesn't exist there at all —
    # that's exactly what made the very first build attempt fail with
    # "Unable to save shortcut ... DirectoryNotFoundException". The registry's
    # User Shell Folders key is the actual source of truth Explorer itself uses; falling
    # back to the naive path only if that lookup fails for some reason (a machine with no
    # redirection at all still has this key, just pointed at the unredirected default).
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "Desktop")
            return os.path.expandvars(value)
    except OSError:
        return os.path.join(os.environ["USERPROFILE"], "Desktop")


def _require(condition, message):
    if not condition:
        print(f"[build.py] {message}", file=sys.stderr)
        sys.exit(1)


def _create_shortcut(link_path: str):
    # `-Command "<script text>"` does NOT bind trailing argv to the script's own param()
    # block the way `-File script.ps1 -Name value` does — tried that first, PowerShell
    # instead tried to run `-LinkPath` itself as a separate command after the inline script
    # ran with every param null, failing with "The shortcut pathname must end with .lnk".
    # `-File` against a real (temp) .ps1 is the correct, standard way to pass named
    # parameters to a script from an external process.
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8") as f:
        f.write(_MAKE_SHORTCUT_PS)
        script_path = f.name
    try:
        subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-NonInteractive", "-File", script_path,
                "-LinkPath", link_path,
                "-TargetPath", VENV_PYTHONW,
                "-Arguments", f'"{WINDOW_SCRIPT}"',
                "-WorkingDir", THIS_DIR,
                "-IconPath", ICON_PATH,
            ],
            check=True,
        )
    finally:
        os.unlink(script_path)
    # Belt and suspenders (see the docstring on _MAKE_SHORTCUT_PS above) — don't trust a
    # 0 exit code alone as proof the .lnk actually exists.
    _require(os.path.exists(link_path), f"powershell.exe exited 0 but {link_path} still doesn't exist — something's wrong beyond what $ErrorActionPreference could catch.")


def main():
    _require(sys.platform == "win32", "build.py's shortcut step is Windows-only (see task.md for macOS/Linux notes).")
    _require(os.path.exists(VENV_PYTHONW), f"Missing {VENV_PYTHONW} — run: uv venv .venv && uv pip install --python .venv -r requirements.txt")
    _require(os.path.exists(WINDOW_SCRIPT), f"Missing {WINDOW_SCRIPT}")
    if not os.path.exists(ICON_PATH):
        print("[build.py] icon.ico not found — generating it from webapp/icon.svg...")
        subprocess.run([os.path.join(THIS_DIR, ".venv", "Scripts", "python.exe"), os.path.join(THIS_DIR, "build_icon.py")], check=True)

    start_menu_link = os.path.join(START_MENU_DIR, SHORTCUT_NAME)
    desktop_link = os.path.join(_real_desktop_dir(), SHORTCUT_NAME)

    _create_shortcut(start_menu_link)
    _create_shortcut(desktop_link)

    print(f"[build.py] Shortcut written: {start_menu_link}")
    print(f"[build.py] Shortcut written: {desktop_link}")
    print("[build.py] Done. Double-click the Desktop shortcut, or search \"Jarvis\" in the Start Menu.")
    print("[build.py] To pin to the taskbar: right-click either shortcut -> \"Pin to taskbar\".")
    print("[build.py] First launch may show a SmartScreen \"unknown publisher\" prompt (unsigned build, expected) -> \"More info\" -> \"Run anyway\".")


if __name__ == "__main__":
    main()

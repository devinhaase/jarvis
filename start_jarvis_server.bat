@echo off
REM start_jarvis_server.bat — launches the Jarvis multi-device server. Placed (as a copy)
REM in your Startup folder, Windows runs this automatically at login — no admin rights
REM needed, no Windows Service to install, no elevated Task Scheduler entry.
REM
REM Points at this project's own dedicated .venv (created after the OneDrive sync incident
REM moved the live deployment to C:\Users\devin\dev\jarvis, away from the hermes-agent venv
REM this used to share) rather than relying on PATH resolution at boot time, which can
REM differ from an interactive login shell's PATH.
REM
REM Runs hidden via Start-Process -WindowStyle Hidden (same reasoning
REM desktop_app/window.py's CREATE_NO_WINDOW uses, and the same pattern
REM start_jarvis_caddy.bat already uses) — a plain "python.exe server.py" line here would
REM pop a visible console window at every login.

powershell.exe -NoProfile -WindowStyle Hidden -Command "Start-Process -FilePath 'C:\Users\devin\dev\jarvis\.venv\Scripts\python.exe' -ArgumentList 'server.py' -WorkingDirectory 'C:\Users\devin\dev\jarvis' -WindowStyle Hidden"

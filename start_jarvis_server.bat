@echo off
REM start_jarvis_server.bat — launches the Jarvis multi-device server. Placed (as a copy)
REM in your Startup folder, Windows runs this automatically at login — no admin rights
REM needed, no Windows Service to install, no elevated Task Scheduler entry.
REM
REM Uses the exact python.exe this project has been running with all along (confirmed to
REM have fastapi/uvicorn/etc. installed) rather than relying on PATH resolution at boot
REM time, which can differ from an interactive login shell's PATH.

cd /d "C:\Users\devin\OneDrive\Desktop\AI Agent\jarvis"
"C:\Users\devin\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe" server.py

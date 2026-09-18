@echo off
REM start_jarvis_caddy.bat — launches Caddy (reverse proxy + HTTPS for ai.dhaaselab.com and
REM the LAN site) at login, same "copy into Startup folder" pattern as
REM start_jarvis_server.bat — no admin rights, no Windows Service. Not previously
REM autostarted at all (Caddy was only ever started manually); added now that server.py
REM itself is autostarted, so the two don't drift out of sync on a reboot.
REM
REM Runs start_caddy.ps1 rather than caddy.exe directly — that script is what reads
REM CLOUDFLARE_API_TOKEN out of .env and sets it in the environment before caddy.exe runs.
REM -WindowStyle Hidden keeps this out of your face at login, same reasoning
REM desktop_app/window.py's CREATE_NO_WINDOW uses for its own server autostart.

powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "C:\Users\devin\dev\jarvis\caddy\start_caddy.ps1"

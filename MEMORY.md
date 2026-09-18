# MEMORY.md — What Jarvis has learned

_Auto-generated from data/semantic_memory.json — do not hand-edit, changes will be overwritten on the next fact/preference update. Last generated: 2026-09-18 19:40 UTC._

## Ongoing facts

- The web GUI (webapp/style.css, index.html, dashboard.html) was redesigned as an Iron Man/Avengers-style HUD: angular clipped panels, a pulsing arc-reactor avatar, depth-layer parallax on scroll, and a Mark II/III color-mood shift tied to integration health (elevated cyan when something is degraded).
- Remote access now uses a local HTTPS cert (mkcert) on the LAN so Twingate/Tailscale are only needed when away from home, not on the same network.
- After a OneDrive sync incident (Sept 2026) corrupted the old project folder, Jarvis now runs from C:\Users\devin\dev\jarvis (its own git clone, outside OneDrive), with autostart scripts (start_jarvis_server.bat, start_jarvis_caddy.bat) in the Windows Startup folder.
- observer.py unifies urgency classification across network_monitor.py/posture_monitor.py/uptime_kuma_monitor.py: a finding recurring 3+ times in 7 days stops sending push notifications and instead rolls into the daily briefing digest.
- goals.py tracks standing objectives (distinct from task_manager.py one-shot tasks) with scheduled check-ins that reschedule from whenever they are actually checked in on.
- documents.py adds local document intelligence: drop a manual/receipt/warranty into data/documents/ and get cited answers via the existing embeddings.py infrastructure.
- ACTIVE_LLM is set to "ollama" - Jarvis now runs entirely on the local Ollama instance (llama3.2 model) with no cloud LLM API key required at all.

## Active projects

_None recorded yet._

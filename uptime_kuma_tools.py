"""
uptime_kuma_tools.py — reads Devin's own Uptime Kuma instance (port 3001, this same
machine) for the Network team's monitoring tools and network_monitor.py's background
"is everything actually up" check.

No credentials, no Twingate gate, unlike firewall_tools.py/nas_tools.py — two real
differences from that Phase 8 work, not an oversight:
  1. Uptime Kuma's "default" status page is published, and a published status page's own
     heartbeat/config endpoints are genuinely public/unauthenticated by Uptime Kuma's own
     design (this is the same data anyone visiting the status page in a browser sees) —
     confirmed live against the real instance, not assumed from docs.
  2. It's co-located with the brain (both on this same machine, per Devin's own
     instruction — "it is on port 3001 on this machine"), not a home-network resource the
     brain reaches over Twingate — so none of Phase 8's "the brain travels, home
     infrastructure needs a protected path" reasoning applies here.

Endpoints (both confirmed live, not from docs alone):
  - GET /api/status-page/default — the monitor list (id, name, type) grouped the way
    Devin's own status page groups them.
  - GET /api/status-page/heartbeat/default — recent heartbeats per monitor id
    ({"status": 0|1|2|3, "time": ..., "msg": ..., "ping": ...}), 0=down, 1=up, 2=pending,
    3=maintenance (Uptime Kuma's own convention) — this module only ever reads the single
    most recent heartbeat per monitor, not the full history (the history is there for a
    dashboard chart; "is it up right now" only needs the last point).
"""

import requests

BASE_URL = "http://localhost:3001"
REQUEST_TIMEOUT_S = 5

_STATUS_LABEL = {0: "down", 1: "up", 2: "pending", 3: "maintenance"}


def get_uptime_kuma_status() -> dict:
    """Tier 1, team=network. Current status of every monitor on Devin's default status
    page — name, state (up/down/pending/maintenance), and latest ping. Degrades to a clear
    error dict (never raises, never crashes the turn) if Uptime Kuma isn't running or the
    status page isn't reachable — this is genuinely optional infrastructure, not something
    every Jarvis install has."""
    try:
        config_resp = requests.get(f"{BASE_URL}/api/status-page/default", timeout=REQUEST_TIMEOUT_S)
        config_resp.raise_for_status()
        config = config_resp.json()

        heartbeat_resp = requests.get(f"{BASE_URL}/api/status-page/heartbeat/default", timeout=REQUEST_TIMEOUT_S)
        heartbeat_resp.raise_for_status()
        heartbeats = heartbeat_resp.json()
    except Exception as e:
        return {"error": "unreachable", "reason": f"Could not reach Uptime Kuma on {BASE_URL}: {e}"}

    heartbeat_list = heartbeats.get("heartbeatList", {})
    uptime_list = heartbeats.get("uptimeList", {})

    monitors = []
    for group in config.get("publicGroupList", []):
        for m in group.get("monitorList", []):
            mid = str(m["id"])
            recent = heartbeat_list.get(mid, [])
            latest = recent[-1] if recent else None
            monitors.append({
                "name": m["name"],
                "group": group.get("name"),
                "status": _STATUS_LABEL.get(latest["status"], "unknown") if latest else "no data",
                "latest_ping_ms": latest.get("ping") if latest else None,
                "uptime_24h_pct": round(uptime_list.get(f"{mid}_24", 0) * 100, 2) if f"{mid}_24" in uptime_list else None,
            })

    return {"monitors": monitors}

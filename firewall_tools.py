"""
firewall_tools.py — Phase 8, section 1: OPNsense-backed tools for the Network and
Cybersecurity teams.

Every function here follows the same three-step shape:
  1. require_twingate_or_refuse(...) — fails closed if there's no protected path home.
  2. Load OPNsense credentials (firewall_opnsense.load_credentials()) — a clear "not
     configured yet" message if Devin hasn't saved API credentials, same "degrade
     gracefully, never crash the turn" posture as the rest of this project's optional
     integrations (Google, Obsidian, Firebase).
  3. Make the actual API call via firewall_opnsense._request(), catch every requests
     exception, and return a plain dict/string a model can read and explain to Devin —
     never let a raw traceback reach the conversation.

Honesty note on OPNsense endpoint paths below: /api/core/firmware/status,
/api/diagnostics/interface/getArp, and /api/diagnostics/interface/getInterfaceStatistics are
confirmed from OPNsense's own published API docs. A couple of the less common ones
(getInterfaceStatus for uptime, the connection-table endpoint) are OPNsense's documented
*shape* (/api/{module}/{controller}/{action}) applied to the most likely controller/action
names, not yet verified against a live instance — this codebase doesn't have real OPNsense
credentials to test against as of writing. Once Devin saves real credentials
(save_opnsense_credentials), re-verify each endpoint against his actual OPNsense version's
API (System -> Access -> API Keys page links the built-in API browser) and correct any that
don't match — flagged here rather than silently assumed correct.
"""

import firewall_opnsense
from twingate_status import require_twingate_or_refuse


def _refuse_or_creds(action_description: str):
    """Shared prelude every function below starts with. Returns (blocked, creds) — if
    blocked is not None, the caller returns it immediately; otherwise creds is a real,
    loaded credentials dict ready to use."""
    blocked = require_twingate_or_refuse(action_description)
    if blocked:
        return blocked, None
    creds = firewall_opnsense.load_credentials()
    if creds is None:
        return {
            "error": "not_configured",
            "reason": "No OPNsense credentials saved yet — use save_opnsense_credentials() "
                      "first (API key/secret from OPNsense's System -> Access -> API Keys).",
        }, None
    return None, creds


def save_opnsense_credentials(base_url: str, api_key: str, api_secret: str) -> dict:
    """Tier 2 (saves local config, reversible/logged — re-running it just overwrites the
    saved pair; it doesn't change anything on OPNsense itself). base_url example:
    'https://192.168.1.1' (LAN) or your Twingate-fronted OPNsense address. Devin provides
    the key/secret himself, generated from OPNsense's own UI — this tool never generates or
    guesses credentials, only stores what it's given, encrypted."""
    firewall_opnsense.save_credentials(base_url, api_key, api_secret)
    return {"saved": True, "base_url": base_url.rstrip("/")}


def get_connected_devices() -> dict:
    """Tier 1, team=network. The firewall's live ARP table — every device currently seen
    on the LAN (IP + MAC pairs), the same shape arp_table_snapshot() already returns for
    the local machine's own ARP table, just sourced from the firewall's vantage point
    instead (sees every device on the network, not just this one host's own table)."""
    blocked, creds = _refuse_or_creds("read connected-device list from the firewall")
    if blocked:
        return blocked
    try:
        resp = firewall_opnsense._request(creds, "GET", "/api/diagnostics/interface/getArp")
        resp.raise_for_status()
        return {"devices": resp.json()}
    except Exception as e:
        return {"error": "request_failed", "reason": str(e)}


def get_bandwidth_usage() -> dict:
    """Tier 1, team=network. Per-interface byte/packet counters from the firewall."""
    blocked, creds = _refuse_or_creds("read bandwidth usage from the firewall")
    if blocked:
        return blocked
    try:
        resp = firewall_opnsense._request(creds, "GET", "/api/diagnostics/interface/getInterfaceStatistics")
        resp.raise_for_status()
        return {"interfaces": resp.json()}
    except Exception as e:
        return {"error": "request_failed", "reason": str(e)}


def get_active_connections() -> dict:
    """Tier 1, team=network. The firewall's current state table (active connections
    passing through it right now)."""
    blocked, creds = _refuse_or_creds("read active connections from the firewall")
    if blocked:
        return blocked
    try:
        resp = firewall_opnsense._request(creds, "GET", "/api/diagnostics/firewall/pf_states")
        resp.raise_for_status()
        return {"connections": resp.json()}
    except Exception as e:
        return {"error": "request_failed", "reason": str(e)}


def get_firewall_uptime() -> dict:
    """Tier 1, team=network. The firewall's own reported uptime/system status."""
    blocked, creds = _refuse_or_creds("read firewall uptime/system status")
    if blocked:
        return blocked
    try:
        resp = firewall_opnsense._request(creds, "GET", "/api/core/firmware/status")
        resp.raise_for_status()
        return {"status": resp.json()}
    except Exception as e:
        return {"error": "request_failed", "reason": str(e)}


def get_firewall_logs(limit: int = 50) -> dict:
    """Tier 1, team=cybersecurity. Recent firewall filter logs, for anomaly detection —
    read-only, this is what network_monitor.py's baseline loop also pulls from."""
    blocked, creds = _refuse_or_creds("read firewall logs")
    if blocked:
        return blocked
    try:
        resp = firewall_opnsense._request(
            creds, "GET", "/api/diagnostics/log/core/firewall", params={"limit": limit}
        )
        resp.raise_for_status()
        return {"logs": resp.json()}
    except Exception as e:
        return {"error": "request_failed", "reason": str(e)}


def get_firewall_traffic_patterns() -> dict:
    """Tier 1, team=cybersecurity. A snapshot combining current interface stats + active
    connections, in the shape network_monitor.py's baseline comparison expects — the
    Cybersecurity team's version of "what does traffic look like right now," distinct from
    Network team's per-interface monitoring above (same underlying data, different lens:
    pattern/anomaly framing rather than raw throughput numbers)."""
    blocked, creds = _refuse_or_creds("read firewall traffic patterns")
    if blocked:
        return blocked
    try:
        stats = firewall_opnsense._request(creds, "GET", "/api/diagnostics/interface/getInterfaceStatistics")
        stats.raise_for_status()
        states = firewall_opnsense._request(creds, "GET", "/api/diagnostics/firewall/pf_states")
        states.raise_for_status()
        return {"interface_stats": stats.json(), "active_connections": states.json()}
    except Exception as e:
        return {"error": "request_failed", "reason": str(e)}


# ---------------------------------------------------------------------------
# Rule-mutating tools — Tier 4, every one, no exceptions (Devin's own instruction: "a
# misconfiguration here can lock me out of my own network or expose it"). role="offense"
# gets these the same dispatch-layer authorization re-check coordinator.py already applies
# to every other offense tool (target/host arg name matched against
# data/authorized_targets.json) — on top of Tier 4's own explicit-confirmation-every-call
# gate, which is the actual enforcement mechanism for "every time, no exceptions."
# ---------------------------------------------------------------------------

def add_firewall_rule(interface: str, action: str, protocol: str, source: str,
                       destination: str, destination_port: str = "", description: str = "",
                       target: str = "") -> dict:
    """Tier 4. Adds ONE firewall rule and applies it immediately — this is the single most
    consequential tool in this module, exactly the kind of change Devin flagged could lock
    him out of his own network. `target` should be the firewall's own address (matches
    Coordinator's dispatch-layer authorization check, which looks for a target/host/query/
    domain argument) — pass the same host you configured in save_opnsense_credentials.
    interface/action/protocol/source/destination/destination_port map directly onto
    OPNsense's own addRule fields; description is stored on the rule itself so it's
    identifiable later as something Jarvis added, not a mystery rule."""
    blocked, creds = _refuse_or_creds(f"add a firewall rule ({description or 'no description given'})")
    if blocked:
        return blocked
    try:
        payload = {"rule": {
            "interface": interface, "action": action, "protocol": protocol,
            "source_net": source, "destination_net": destination,
            "destination_port": destination_port,
            "description": description or "Added by Jarvis",
        }}
        resp = firewall_opnsense._request(creds, "POST", "/api/firewall/filter/addRule", json=payload)
        resp.raise_for_status()
        result = resp.json()
        # OPNsense requires a separate apply call — a rule that's added but never applied
        # is silently inert, which would be a worse outcome here than a clear error: better
        # to surface the apply step failing than to leave Devin thinking a rule is live
        # when it isn't.
        apply_resp = firewall_opnsense._request(creds, "POST", "/api/firewall/filter/apply")
        apply_resp.raise_for_status()
        return {"added": True, "rule": result, "applied": True}
    except Exception as e:
        return {"error": "request_failed", "reason": str(e)}


def remove_firewall_rule(rule_uuid: str, target: str = "") -> dict:
    """Tier 4. Removes one rule by its OPNsense-assigned uuid (from add_firewall_rule's
    own return value, or get_connected_devices/a prior searchRule-style lookup) and applies
    the change."""
    blocked, creds = _refuse_or_creds(f"remove firewall rule {rule_uuid}")
    if blocked:
        return blocked
    try:
        resp = firewall_opnsense._request(creds, "POST", f"/api/firewall/filter/delRule/{rule_uuid}")
        resp.raise_for_status()
        apply_resp = firewall_opnsense._request(creds, "POST", "/api/firewall/filter/apply")
        apply_resp.raise_for_status()
        return {"removed": True, "rule_uuid": rule_uuid, "applied": True}
    except Exception as e:
        return {"error": "request_failed", "reason": str(e)}


def toggle_port(rule_uuid: str, enable: bool, target: str = "") -> dict:
    """Tier 4. Opens or closes a port by enabling/disabling an existing rule (rather than
    deleting it outright) — the lower-risk, reversible way to flip a port's state without
    losing the rule's own configuration."""
    blocked, creds = _refuse_or_creds(f"{'enable' if enable else 'disable'} firewall rule {rule_uuid}")
    if blocked:
        return blocked
    try:
        resp = firewall_opnsense._request(
            creds, "POST", f"/api/firewall/filter/toggleRule/{rule_uuid}/{1 if enable else 0}"
        )
        resp.raise_for_status()
        apply_resp = firewall_opnsense._request(creds, "POST", "/api/firewall/filter/apply")
        apply_resp.raise_for_status()
        return {"toggled": True, "rule_uuid": rule_uuid, "enabled": enable, "applied": True}
    except Exception as e:
        return {"error": "request_failed", "reason": str(e)}

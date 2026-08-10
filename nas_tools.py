"""
nas_tools.py — Phase 8, section 2: UGREEN NAS (UGOS)-backed tools for the IT team.

Same three-step shape as firewall_tools.py: require_twingate_or_refuse() first, then load
saved NAS credentials (clear "not configured" message if missing), then the actual API
call via nas_ugreen._request(), with every exception caught and turned into a plain dict
rather than a raw traceback.

Endpoint paths below are the ones confirmed from the reference implementation
(nas_ugreen.py's module docstring explains where they came from) — real, not guessed, but
still unofficial and worth re-checking against Devin's actual NAS once credentials are
saved and this can be tested live.
"""

import nas_ugreen
from twingate_status import require_twingate_or_refuse


def _refuse_or_creds(action_description: str):
    blocked = require_twingate_or_refuse(action_description)
    if blocked:
        return blocked, None
    creds = nas_ugreen.load_credentials()
    if creds is None:
        return {
            "error": "not_configured",
            "reason": "No NAS credentials saved yet — use save_nas_credentials() first "
                      "(your NAS's own username/password).",
        }, None
    return None, creds


def save_nas_credentials(base_url: str, username: str, password: str) -> dict:
    """Tier 2 (saves local config, reversible/logged). base_url example:
    'https://192.168.1.50:9443' — whatever address reaches the NAS from wherever the brain
    is (LAN IP or Twingate-fronted address). Devin's own NAS login, not a separate service
    account — UGOS has no API-key concept to scope this down further; recommend using a
    dedicated NAS user with the minimum permissions this integration actually needs (not
    the primary admin account) if UGOS supports creating one, same "least privilege"
    reasoning the Google integration's narrow OAuth scopes already follow."""
    nas_ugreen.save_credentials(base_url, username, password)
    return {"saved": True, "base_url": base_url.rstrip("/")}


def get_nas_storage_health() -> dict:
    """Tier 1, team=it. Disk-level health from the NAS's storage subsystem."""
    blocked, creds = _refuse_or_creds("read NAS storage health")
    if blocked:
        return blocked
    try:
        return {"disks": nas_ugreen._request(creds, "GET", "/ugreen/v2/storage/disk/list")}
    except Exception as e:
        return {"error": "request_failed", "reason": str(e)}


def get_nas_capacity() -> dict:
    """Tier 1, team=it. Storage pool capacity/usage."""
    blocked, creds = _refuse_or_creds("read NAS storage capacity")
    if blocked:
        return blocked
    try:
        return {"pools": nas_ugreen._request(creds, "GET", "/ugreen/v1/storage/pool/list")}
    except Exception as e:
        return {"error": "request_failed", "reason": str(e)}


def get_nas_backup_status() -> dict:
    """Tier 1, team=it. Recent/scheduled backup task status — this is also what
    network_monitor.py's baseline loop watches for "backup failure" notifications."""
    blocked, creds = _refuse_or_creds("read NAS backup status")
    if blocked:
        return blocked
    try:
        return {
            "tasks": nas_ugreen._request(
                creds, "GET", "/ugreen/v2/web/syncbackup/task/list?page=1&size=1000"
            )
        }
    except Exception as e:
        return {"error": "request_failed", "reason": str(e)}


def get_nas_running_services() -> dict:
    """Tier 1, team=it. What's currently running on the NAS (system/task-manager view)."""
    blocked, creds = _refuse_or_creds("read NAS running services")
    if blocked:
        return blocked
    try:
        return {"status": nas_ugreen._request(creds, "GET", "/ugreen/v1/taskmgr/stat/get_all")}
    except Exception as e:
        return {"error": "request_failed", "reason": str(e)}


def create_nas_backup(source_path: str, description: str = "") -> dict:
    """Tier 2 (reversible/logged, not destructive — creating a backup doesn't remove or
    overwrite anything). Triggers a one-off backup job for source_path. Exact endpoint for
    triggering an ad-hoc job (as opposed to just listing scheduled ones, which
    get_nas_backup_status already covers) isn't confirmed from the reference project —
    flagging this explicitly rather than guessing a POST body shape with no real source to
    check it against; verify against UGOS's own web UI network tab (browser dev tools while
    triggering a manual backup there) once credentials are configured, and correct this
    function's endpoint/payload to match before relying on it."""
    blocked, creds = _refuse_or_creds(f"create a NAS backup of {source_path}")
    if blocked:
        return blocked
    try:
        return {
            "result": nas_ugreen._request(
                creds, "POST", "/ugreen/v2/web/syncbackup/task/create",
                json={"source_path": source_path, "description": description or "Created by Jarvis"},
            )
        }
    except Exception as e:
        return {"error": "request_failed", "reason": str(e)}


def read_nas_file(path: str, max_bytes: int = 200_000) -> dict:
    """Tier 2 (read-only, but actual file content — not just metadata — could expose
    sensitive data, so this sits a tier above the pure-status reads above). Exact
    file-download endpoint isn't confirmed from the reference project either — same
    verify-against-the-real-UI caveat as create_nas_backup applies here."""
    blocked, creds = _refuse_or_creds(f"read NAS file {path}")
    if blocked:
        return blocked
    try:
        result = nas_ugreen._request(
            creds, "GET", "/ugreen/v1/file/download", params={"path": path}
        )
        return {"path": path, "content": str(result)[:max_bytes]}
    except Exception as e:
        return {"error": "request_failed", "reason": str(e)}


# ---------------------------------------------------------------------------
# Config/permission-mutating tools — Tier 4, role="offense" (same reasoning as
# firewall_tools.py's rule-mutating tools: gets the Coordinator's dispatch-layer
# authorization re-check for free, on top of Tier 4's own explicit-confirmation gate).
# ---------------------------------------------------------------------------

def delete_nas_file(path: str, target: str = "") -> dict:
    """Tier 4. Deletes one file/path on the NAS — irreversible from Jarvis's side (whatever
    UGOS's own trash/recycle behavior is, if any, is UGOS's, not something this tool
    relies on). Endpoint unverified against a live NAS — same caveat as above."""
    blocked, creds = _refuse_or_creds(f"delete NAS file {path}")
    if blocked:
        return blocked
    try:
        result = nas_ugreen._request(
            creds, "POST", "/ugreen/v1/file/delete", json={"path": path}
        )
        return {"deleted": True, "path": path, "result": result}
    except Exception as e:
        return {"error": "request_failed", "reason": str(e)}


def update_nas_config(setting: str, value: str, target: str = "") -> dict:
    """Tier 4. Changes one NAS-level config setting. Deliberately generic (setting/value)
    rather than one function per possible setting — UGOS's actual config surface isn't
    documented, so this is the honest shape until specific settings are confirmed needed;
    each real call still goes through the same Tier-4 confirmation regardless of which
    setting it targets."""
    blocked, creds = _refuse_or_creds(f"change NAS config setting '{setting}'")
    if blocked:
        return blocked
    try:
        result = nas_ugreen._request(
            creds, "POST", "/ugreen/v1/sysinfo/config/set", json={setting: value}
        )
        return {"updated": True, "setting": setting, "value": value, "result": result}
    except Exception as e:
        return {"error": "request_failed", "reason": str(e)}


def modify_nas_share_permissions(share_name: str, user: str, permission: str, target: str = "") -> dict:
    """Tier 4. Changes one user's permission level on one shared folder — same
    unverified-endpoint caveat as the rest of this module's write path."""
    blocked, creds = _refuse_or_creds(f"change permissions on share '{share_name}' for {user}")
    if blocked:
        return blocked
    try:
        result = nas_ugreen._request(
            creds, "POST", "/ugreen/v1/share/permission/set",
            json={"share": share_name, "user": user, "permission": permission},
        )
        return {"updated": True, "share": share_name, "user": user, "permission": permission, "result": result}
    except Exception as e:
        return {"error": "request_failed", "reason": str(e)}

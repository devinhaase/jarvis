"""
device_registry.py — Per-device authentication + capability authorization for the
multi-device server. A device's ALLOWED capabilities are fixed at registration time —
something only someone with file access to this machine can do — and a connecting client
can request less than that at connect time, never more. Same "explicit allowlist, enforced
in code, reloaded per check" pattern as auth_check.py's AuthorizationCheck, applied to
devices instead of scan targets.
"""

import os
import json
import secrets
import time

DEVICES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "devices.json")


class DeviceRegistry:
    def __init__(self, path: str = None):
        self.path = path or DEVICES_FILE
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            self._data = {"devices": {}}
            self._save()
        else:
            with open(self.path, 'r') as f:
                self._data = json.load(f)

    def _save(self):
        with open(self.path, 'w') as f:
            json.dump(self._data, f, indent=2)

    def register(self, device_id: str, name: str = "", capabilities: list = None) -> str:
        """Create (or reissue) a device entry, returning its new bearer token. The token is
        the caller's to keep — it's stored here only for future validation, never re-shown."""
        token = secrets.token_hex(24)
        self._data.setdefault("devices", {})[device_id] = {
            "token": token,
            "name": name or device_id,
            "allowed_capabilities": capabilities or [],
            "created": time.time(),
        }
        self._save()
        return token

    def revoke(self, device_id: str) -> bool:
        if device_id in self._data.get("devices", {}):
            del self._data["devices"][device_id]
            self._save()
            return True
        return False

    def validate(self, device_id: str, token: str):
        """Returns the device's allowed_capabilities list if the token matches, else None.
        Reloads from disk first so a revoke takes effect without restarting the server."""
        self._load()
        entry = self._data.get("devices", {}).get(device_id)
        if not entry:
            return None
        if not token or not secrets.compare_digest(entry["token"], token):
            return None
        return entry.get("allowed_capabilities", [])

    def list_devices(self) -> dict:
        self._load()
        return {
            k: {
                "name": v.get("name"),
                "allowed_capabilities": v.get("allowed_capabilities", []),
                "created": v.get("created"),
            }
            for k, v in self._data.get("devices", {}).items()
        }

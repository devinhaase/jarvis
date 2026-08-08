import json
import os
import ipaddress

AUTHORIZED_TARGETS_FILE = os.path.join("data", "authorized_targets.json")

class AuthorizationError(Exception):
    pass

class AuthorizationCheck:
    def __init__(self):
        self._load()

    def _load(self):
        if not os.path.exists(AUTHORIZED_TARGETS_FILE):
            raise FileNotFoundError(
                f"Authorization file not found at '{AUTHORIZED_TARGETS_FILE}'.\n"
                "Security tools cannot run without an explicit authorization list.\n"
                "Create the file and add your own hosts/networks to it."
            )
        with open(AUTHORIZED_TARGETS_FILE, 'r') as f:
            self._data = json.load(f)

    def is_authorized(self, target: str) -> bool:
        """
        Returns True if target is in the explicit allowlist.
        Raises AuthorizationError otherwise — does not silently fail.
        """
        # Reload each time so edits take effect without restart
        self._load()

        # Check explicit host list
        if target in self._data.get("hosts", []):
            return True

        # Check explicit domain list
        if target in self._data.get("domains", []):
            return True

        # Check network ranges
        try:
            target_ip = ipaddress.ip_address(target)
            for net_str in self._data.get("networks", []):
                if target_ip in ipaddress.ip_network(net_str, strict=False):
                    return True
        except ValueError:
            pass  # target is a hostname, not an IP — already checked above

        raise AuthorizationError(
            f"Target '{target}' is NOT in your authorized targets list.\n"
            f"To authorize it, add it to '{AUTHORIZED_TARGETS_FILE}' manually.\n"
            "This check exists to prevent accidental scanning of systems you don't own."
        )

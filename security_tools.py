"""
Security Specialist Tools for Jarvis.

Scope: Systems and accounts Devin owns or has explicit documented authorization to test.
Authorization is enforced by AuthorizationCheck — not by comments, not by prompts.

Out of scope (hard-blocked at tool registration, not by LLM prompt):
- Exploit code generation
- Scanning targets not in authorized_targets.json
- Credential testing against third-party live services
- Network traffic interception / MITM
"""

import subprocess
import os
import shutil
import json
import hashlib
import requests as http_requests
from auth_check import AuthorizationCheck, AuthorizationError

_auth = AuthorizationCheck()

# ---------------------------------------------------------------------------
# PORT SCANNING
# ---------------------------------------------------------------------------

def scan_ports(target: str, port_range: str = "1-1024") -> dict:
    """
    Scan open ports on an authorized target using nmap.
    Requires nmap to be installed: https://nmap.org/download.html
    """
    _auth.is_authorized(target)  # Raises AuthorizationError if not in allowlist

    try:
        result = subprocess.check_output(
            ["nmap", "-sV", "--open", f"-p{port_range}", target],
            text=True, stderr=subprocess.STDOUT, timeout=60
        )
        return {"target": target, "status": "success", "output": result}
    except FileNotFoundError:
        return {
            "target": target,
            "status": "error",
            "output": "nmap is not installed. Download it from https://nmap.org/download.html"
        }
    except subprocess.TimeoutExpired:
        return {"target": target, "status": "error", "output": "Scan timed out after 60 seconds."}
    except subprocess.CalledProcessError as e:
        return {"target": target, "status": "error", "output": e.output}


# ---------------------------------------------------------------------------
# LOCAL SECURITY POSTURE
# ---------------------------------------------------------------------------

def get_security_posture() -> dict:
    """
    Check local machine security posture:
    - Windows Defender / Firewall status
    - Pending updates
    - Listening services (netstat)
    """
    results = {}

    # Windows Defender status
    try:
        defender = subprocess.check_output(
            ["powershell", "-Command",
             "Get-MpComputerStatus | Select-Object -Property AntivirusEnabled,RealTimeProtectionEnabled,AntispywareEnabled | ConvertTo-Json"],
            text=True, timeout=15
        )
        results["windows_defender"] = defender.strip()
    except Exception as e:
        results["windows_defender"] = f"Error: {e}"

    # Firewall status
    try:
        firewall = subprocess.check_output(
            ["powershell", "-Command",
             "Get-NetFirewallProfile | Select-Object Name,Enabled | ConvertTo-Json"],
            text=True, timeout=15
        )
        results["firewall"] = firewall.strip()
    except Exception as e:
        results["firewall"] = f"Error: {e}"

    # Pending Windows updates
    try:
        updates = subprocess.check_output(
            ["powershell", "-Command",
             "(New-Object -ComObject Microsoft.Update.Session).CreateUpdateSearcher().Search('IsInstalled=0').Updates.Count"],
            text=True, timeout=30
        )
        results["pending_updates"] = updates.strip() + " pending updates"
    except Exception as e:
        results["pending_updates"] = f"Error checking updates: {e}"

    # Listening ports / services
    try:
        netstat = subprocess.check_output(
            ["powershell", "-Command",
             "Get-NetTCPConnection -State Listen | Select-Object LocalAddress,LocalPort,OwningProcess | Sort-Object LocalPort | ConvertTo-Json"],
            text=True, timeout=15
        )
        results["listening_services"] = netstat.strip()
    except Exception as e:
        results["listening_services"] = f"Error: {e}"

    return results


# ---------------------------------------------------------------------------
# CREDENTIAL HYGIENE
# ---------------------------------------------------------------------------

def check_password_breach(password: str) -> dict:
    """
    Check if a password has appeared in a known breach using the HaveIBeenPwned
    k-anonymity API. Only the first 5 characters of the SHA-1 hash are sent —
    the full password never leaves the device.
    """
    sha1 = hashlib.sha1(password.encode('utf-8')).hexdigest().upper()
    prefix, suffix = sha1[:5], sha1[5:]

    try:
        response = http_requests.get(
            f"https://api.pwnedpasswords.com/range/{prefix}",
            headers={"Add-Padding": "true"},
            timeout=10
        )
        response.raise_for_status()

        hashes = (line.split(':') for line in response.text.splitlines())
        for h, count in hashes:
            if h == suffix:
                return {
                    "breached": True,
                    "count": int(count),
                    "note": f"This password appeared {count} times in known data breaches. Change it immediately."
                }
        return {"breached": False, "note": "Not found in known breach databases. Still use a strong, unique password."}
    except Exception as e:
        return {"breached": None, "error": str(e)}


def check_email_breach(email: str) -> dict:
    """
    Check if an email address appears in known data breaches via HaveIBeenPwned API.
    Requires a HIBP_API_KEY in your .env file (https://haveibeenpwned.com/API/Key).
    """
    api_key = os.getenv("HIBP_API_KEY")
    if not api_key:
        return {
            "error": "HIBP_API_KEY not set in .env. "
                     "Get a key at https://haveibeenpwned.com/API/Key and add it to your .env file."
        }
    try:
        response = http_requests.get(
            f"https://haveibeenpwned.com/api/v3/breachedaccount/{email}",
            headers={"hibp-api-key": api_key, "user-agent": "JarvisAgent/1.0"},
            timeout=10
        )
        if response.status_code == 404:
            return {"email": email, "breached": False, "note": "No breaches found for this email."}
        response.raise_for_status()
        breaches = response.json()
        names = [b['Name'] for b in breaches]
        return {
            "email": email,
            "breached": True,
            "breach_count": len(names),
            "breaches": names
        }
    except Exception as e:
        return {"error": str(e)}


def check_credential_hygiene(max_breach_checks: int = 25) -> dict:
    """
    Analyze your local Bitwarden vault for password reuse and known-breached passwords,
    via the official `bw` CLI. Jarvis never touches your master password: you run
    `bw login` (once) and `bw unlock --raw` (per session) yourself, and export the printed
    session token as BW_SESSION — this tool only reads what an already-unlocked session
    exposes, it never handles the credential that unlocks it.

    Decrypted vault data lives in memory only for the duration of this call — never written
    to disk, never logged. Output only ever names which ITEMS share a password (by title),
    never the password value itself; breach results are counts, not the password.
    """
    bw_path = shutil.which("bw")
    if not bw_path:
        return {
            "error": "Bitwarden CLI ('bw') not found on PATH. "
                     "Install: https://bitwarden.com/help/cli/#download-and-install"
        }

    session = os.getenv("BW_SESSION")
    if not session:
        return {
            "error": "BW_SESSION not set. Run these yourself — Jarvis never handles your "
                     "master password: `bw login` once, then `bw unlock --raw` each session, "
                     "and export the value it prints as BW_SESSION in your shell environment "
                     "(not in .env — this is a short-lived session token, not a saved secret)."
        }

    try:
        result = subprocess.run(
            [bw_path, "list", "items", "--session", session],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return {"error": f"bw CLI error: {result.stderr.strip() or 'unknown error — is the session still unlocked?'}"}
        items = json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        return {"error": "bw CLI timed out after 30 seconds."}
    except json.JSONDecodeError:
        return {"error": "Could not parse bw CLI output — is BW_SESSION valid and unlocked?"}
    except Exception as e:
        return {"error": f"Failed to read vault: {e}"}

    return _analyze_vault_items(items, max_breach_checks=max_breach_checks)


def _analyze_vault_items(items: list, max_breach_checks: int = 25) -> dict:
    """
    Pure analysis over already-fetched vault items — split out from check_credential_hygiene
    so the reuse/breach-detection logic is testable against synthetic fixtures without a real
    bw session. Takes ownership of `items` (the only reference to decrypted vault data) and
    lets it go out of scope when this returns.
    """
    by_password = {}
    total_logins = 0

    for item in items:
        login = item.get("login") or {}
        password = login.get("password")
        if not password:
            continue
        total_logins += 1
        name = item.get("name", "Unnamed item")
        by_password.setdefault(password, []).append(name)

    reused_groups = [
        {"item_names": names, "reuse_count": len(names)}
        for names in by_password.values() if len(names) > 1
    ]
    reused_groups.sort(key=lambda g: -g["reuse_count"])

    unique_passwords = list(by_password.keys())
    checked = 0
    breached_items = []
    truncated = False
    for pw in unique_passwords:
        if checked >= max_breach_checks:
            truncated = True
            break
        checked += 1
        result = check_password_breach(pw)
        if result.get("breached"):
            breached_items.append({"item_names": by_password[pw], "breach_count": result.get("count")})

    return {
        "total_login_items": total_logins,
        "unique_passwords": len(unique_passwords),
        "reused_password_groups": reused_groups,
        "reused_password_count": sum(g["reuse_count"] for g in reused_groups),
        "breached_items": breached_items,
        "passwords_checked_against_breach_db": checked,
        "breach_check_truncated": truncated,
        "note": (
            f"Checked {checked}/{len(unique_passwords)} unique passwords against the breach "
            "database (raise max_breach_checks to check the rest; each check sends only a "
            "5-char hash prefix, never the password itself)." if truncated else
            "All unique passwords checked against the breach database (k-anonymity — only a "
            "5-char hash prefix per password was ever sent, never the password itself)."
        ),
    }


# ---------------------------------------------------------------------------
# ETHICAL HACKING REFERENCE (educational only — no exploit generation)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# POSTURE DASHBOARD (rendering only — get_security_posture() does the collection)
# ---------------------------------------------------------------------------

def render_posture_dashboard(posture: dict):
    """
    Turn the raw dict from get_security_posture() into a rich renderable dashboard.
    Tolerant of partial/failed sections — a PowerShell error in one section
    renders as raw text instead of crashing the whole dashboard.
    """
    import json as _json
    from rich.table import Table
    from rich.panel import Panel
    from rich.console import Group
    from rich.text import Text

    sections = []

    # --- Windows Defender ---
    defender_table = Table(title="Windows Defender", show_header=True, header_style="bold cyan", border_style="dim")
    defender_table.add_column("Setting")
    defender_table.add_column("Status", justify="center")
    try:
        d = _json.loads(posture.get("windows_defender", "{}"))
        if isinstance(d, list):
            d = d[0] if d else {}
        for key in ("AntivirusEnabled", "RealTimeProtectionEnabled", "AntispywareEnabled"):
            val = d.get(key)
            status = "[green]ON[/green]" if val else "[red]OFF[/red]" if val is not None else "[dim]unknown[/dim]"
            defender_table.add_row(key, status)
    except Exception:
        defender_table.add_row("(unparsed)", str(posture.get("windows_defender", ""))[:80])
    sections.append(defender_table)

    # --- Firewall ---
    fw_table = Table(title="Firewall Profiles", show_header=True, header_style="bold cyan", border_style="dim")
    fw_table.add_column("Profile")
    fw_table.add_column("Enabled", justify="center")
    try:
        f = _json.loads(posture.get("firewall", "[]"))
        if isinstance(f, dict):
            f = [f]
        for profile in f:
            enabled = profile.get("Enabled")
            status = "[green]ON[/green]" if enabled else "[red]OFF[/red]"
            fw_table.add_row(str(profile.get("Name", "?")), status)
    except Exception:
        fw_table.add_row("(unparsed)", str(posture.get("firewall", ""))[:80])
    sections.append(fw_table)

    # --- Patch status ---
    updates_text = str(posture.get("pending_updates", "unknown"))
    update_style = "green" if updates_text.strip().startswith("0 ") else "yellow"
    sections.append(Panel(Text(updates_text, style=update_style), title="Patch Status", border_style="dim"))

    # --- Listening services ---
    svc_table = Table(title="Listening Services", show_header=True, header_style="bold cyan", border_style="dim")
    svc_table.add_column("Address")
    svc_table.add_column("Port", justify="right")
    svc_table.add_column("PID", justify="right")
    try:
        svcs = _json.loads(posture.get("listening_services", "[]"))
        if isinstance(svcs, dict):
            svcs = [svcs]
        for s in svcs[:20]:
            svc_table.add_row(str(s.get("LocalAddress", "?")), str(s.get("LocalPort", "?")), str(s.get("OwningProcess", "?")))
        if len(svcs) > 20:
            svc_table.add_row("...", f"+{len(svcs) - 20} more", "")
        if not svcs:
            svc_table.add_row("(none reported)", "", "")
    except Exception:
        svc_table.add_row("(unparsed)", str(posture.get("listening_services", ""))[:80], "")
    sections.append(svc_table)

    return Panel(Group(*sections), title="[bold]Security Posture Dashboard[/bold]", border_style="magenta")


def get_security_reference(topic: str) -> str:
    """
    Route a security/ethical hacking topic to the LLM for conceptual explanation.
    This tool exists so the Coordinator knows to frame answers educationally,
    not as operational attack guidance.
    Returns a string the LLM will use as context for its response.
    """
    # This is a pass-through — the LLM handles the actual explanation.
    # The tool's existence signals to the LLM that educational security topics
    # should be answered with conceptual depth rather than deflection.
    return (
        f"Educational security topic requested: '{topic}'. "
        "Answer with technical depth appropriate for someone studying ethical hacking or a security certification. "
        "Explain concepts, techniques, and defensive implications. "
        "Do not generate working exploit code or provide step-by-step attack instructions against specific real systems."
    )

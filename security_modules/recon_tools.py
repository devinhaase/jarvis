"""
security_modules/recon_tools.py — Ethical hacking recon tools.
All network tools require passing AuthorizationCheck.
"""

import os
import sys
import json
import subprocess
import re
from datetime import datetime

# Path fix so we can import from parent
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from auth_check import AuthorizationCheck, AuthorizationError

_auth = AuthorizationCheck()

os.makedirs(os.path.join("data", "pentest_reports"), exist_ok=True)


# ---------------------------------------------------------------------------
# CVE LOOKUP (NIST NVD API — no key required)
# ---------------------------------------------------------------------------

def lookup_cve(search_term: str) -> dict:
    """Search NIST NVD for CVEs matching a service/version string."""
    try:
        import requests
        url = "https://services.nvd.nist.gov/rest/json/cves/2.0"
        params = {"keywordSearch": search_term, "resultsPerPage": 5}
        r = requests.get(url, params=params, timeout=15,
                         headers={"User-Agent": "JarvisAgent/1.0"})
        r.raise_for_status()
        data = r.json()
        results = []
        for item in data.get("vulnerabilities", []):
            cve = item.get("cve", {})
            cve_id = cve.get("id", "")
            descs = cve.get("descriptions", [])
            desc = next((d["value"] for d in descs if d["lang"] == "en"), "No description")
            metrics = cve.get("metrics", {})
            severity = "UNKNOWN"
            for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
                m = metrics.get(key, [])
                if m:
                    severity = m[0].get("cvssData", {}).get("baseSeverity", "UNKNOWN")
                    break
            published = cve.get("published", "")[:10]
            results.append({"cve_id": cve_id, "severity": severity,
                            "description": desc[:300], "published": published})
        return {"search_term": search_term, "count": len(results), "results": results}
    except Exception as e:
        return {"search_term": search_term, "error": str(e), "results": []}


# ---------------------------------------------------------------------------
# EXPLOIT-DB SEARCH
# ---------------------------------------------------------------------------

def search_exploitdb(search_term: str) -> dict:
    """Search Exploit-DB for public exploits matching a search term."""
    try:
        import requests
        url = f"https://www.exploit-db.com/search"
        params = {"q": search_term}
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json, text/javascript, */*",
            "X-Requested-With": "XMLHttpRequest"
        }
        r = requests.get(url, params=params, headers=headers, timeout=15)
        r.raise_for_status()
        try:
            data = r.json()
            exploits = []
            for row in data.get("data", [])[:10]:
                exploits.append({
                    "id": row.get("id", ""),
                    "title": row.get("description", ""),
                    "type": row.get("type", {}).get("name", ""),
                    "platform": row.get("platform", {}).get("name", ""),
                    "date": row.get("date_published", ""),
                    "link": f"https://www.exploit-db.com/exploits/{row.get('id', '')}"
                })
            return {"search_term": search_term, "count": len(exploits), "results": exploits}
        except Exception:
            return {
                "search_term": search_term,
                "note": "Direct search at https://www.exploit-db.com/search?q=" + search_term,
                "results": []
            }
    except Exception as e:
        return {"search_term": search_term, "error": str(e), "results": []}


# ---------------------------------------------------------------------------
# FULL RECON PIPELINE
# ---------------------------------------------------------------------------

def run_recon_pipeline(target: str) -> dict:
    """
    Full recon pipeline: host discovery → port scan → service ID → CVE lookup.
    Target must be in authorized_targets.json.
    """
    _auth.is_authorized(target)  # Hard stop if not authorized

    result = {
        "target": target,
        "timestamp": datetime.now().isoformat(),
        "host_status": "unknown",
        "open_ports": [],
        "cve_summary": [],
        "summary": ""
    }

    # Step 1: Host discovery
    try:
        ping = subprocess.check_output(
            ["nmap", "-sn", target], text=True, timeout=30, stderr=subprocess.STDOUT
        )
        result["host_status"] = "up" if "Host is up" in ping else "down/filtered"
        result["host_discovery_raw"] = ping.strip()
    except FileNotFoundError:
        result["error"] = "nmap not installed. Download: https://nmap.org/download.html"
        return result
    except subprocess.TimeoutExpired:
        result["host_status"] = "timeout"
    except subprocess.CalledProcessError as e:
        result["host_status"] = "error"
        result["host_discovery_raw"] = e.output

    if result["host_status"] != "up":
        result["summary"] = f"Host {target} appears to be down or filtered. No further scanning attempted."
        return result

    # Step 2: Port + service scan
    try:
        scan = subprocess.check_output(
            ["nmap", "-sV", "--open", "-p1-1024", target],
            text=True, timeout=120, stderr=subprocess.STDOUT
        )
        result["port_scan_raw"] = scan

        # Parse open ports
        port_pattern = re.compile(
            r"(\d+)/tcp\s+open\s+(\S+)\s*(.*)"
        )
        for match in port_pattern.finditer(scan):
            port, service, version = match.group(1), match.group(2), match.group(3).strip()
            entry = {"port": port, "service": service, "version": version, "cves": []}

            # CVE lookup per service
            cve_query = f"{service} {version}".strip()
            if version:
                cve_data = lookup_cve(cve_query)
                entry["cves"] = cve_data.get("results", [])

            result["open_ports"].append(entry)

    except subprocess.TimeoutExpired:
        result["port_scan_raw"] = "Scan timed out after 120 seconds."
    except subprocess.CalledProcessError as e:
        result["port_scan_raw"] = e.output

    # Step 3: Summary
    port_count = len(result["open_ports"])
    cve_total = sum(len(p["cves"]) for p in result["open_ports"])
    critical = sum(
        1 for p in result["open_ports"]
        for c in p["cves"] if c.get("severity") in ("CRITICAL", "HIGH")
    )
    result["summary"] = (
        f"{target}: {port_count} open ports found, {cve_total} CVEs matched "
        f"({critical} CRITICAL/HIGH). Full details in open_ports array."
    )
    return result


# ---------------------------------------------------------------------------
# SHODAN LOOKUP
# ---------------------------------------------------------------------------

def shodan_lookup(query: str) -> dict:
    """
    Look up your own IP/domain on Shodan. Requires SHODAN_API_KEY in .env.
    Gated by AuthorizationCheck even though the lookup itself is passive —
    the query still names a specific target, and this tool must not become
    a way to run recon on anyone else's IP/domain.
    """
    _auth.is_authorized(query)  # Raises AuthorizationError if not in allowlist

    api_key = os.getenv("SHODAN_API_KEY")
    if not api_key:
        return {
            "error": "SHODAN_API_KEY not set in .env. Get a free key at https://shodan.io — "
                     "then add: SHODAN_API_KEY=your_key_here"
        }
    try:
        import requests
        r = requests.get(
            f"https://api.shodan.io/shodan/host/{query}",
            params={"key": api_key}, timeout=15
        )
        if r.status_code == 404:
            return {"query": query, "found": False, "note": "Host not indexed by Shodan."}
        r.raise_for_status()
        data = r.json()
        return {
            "query": query,
            "found": True,
            "ip": data.get("ip_str"),
            "org": data.get("org"),
            "country": data.get("country_name"),
            "open_ports": data.get("ports", []),
            "vulns": list(data.get("vulns", {}).keys()),
            "hostnames": data.get("hostnames", []),
            "last_update": data.get("last_update")
        }
    except Exception as e:
        return {"query": query, "error": str(e)}


# ---------------------------------------------------------------------------
# LOCAL PRIVILEGE ESCALATION ENUM (local machine only — always authorized)
# ---------------------------------------------------------------------------

def run_privesc_enum() -> str:
    """
    Run privilege escalation enumeration on the local machine.
    Local machine is always implicitly authorized.
    """
    results = []

    checks = {
        "Current User Privileges": "whoami /priv",
        "Unquoted Service Paths": (
            "Get-WmiObject Win32_Service | "
            "Where-Object {$_.PathName -notmatch '\"' -and $_.PathName -match ' '} | "
            "Select-Object Name, PathName | Format-Table -AutoSize"
        ),
        "AlwaysInstallElevated (HKCU)": (
            "try { (Get-ItemProperty 'HKCU:\\SOFTWARE\\Policies\\Microsoft\\Windows\\Installer' "
            "-ErrorAction Stop).AlwaysInstallElevated } catch { 'Key not found (good)' }"
        ),
        "AlwaysInstallElevated (HKLM)": (
            "try { (Get-ItemProperty 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\Installer' "
            "-ErrorAction Stop).AlwaysInstallElevated } catch { 'Key not found (good)' }"
        ),
        "Scheduled Tasks (SYSTEM-level)": (
            "Get-ScheduledTask | Where-Object {$_.Principal.RunLevel -eq 'Highest'} | "
            "Select-Object TaskName, TaskPath | Format-Table -AutoSize"
        ),
        "Writable Folders in PATH": (
            "$env:PATH.Split(';') | ForEach-Object { "
            "if (Test-Path $_) { $acl = Get-Acl $_; "
            "'$_ : ' + ($acl.Access | Where-Object {$_.FileSystemRights -match 'Write' "
            "-and $_.IdentityReference -match 'Users'} | Select -First 1).IdentityReference } }"
        )
    }

    for check_name, cmd in checks.items():
        try:
            out = subprocess.check_output(
                ["powershell", "-Command", cmd],
                text=True, timeout=20, stderr=subprocess.STDOUT
            ).strip()
            results.append(f"=== {check_name} ===\n{out if out else '(no output)'}\n")
        except subprocess.TimeoutExpired:
            results.append(f"=== {check_name} ===\nTimeout\n")
        except Exception as e:
            results.append(f"=== {check_name} ===\nError: {e}\n")

    return "\n".join(results)


TOOLS = {}  # Registered by plugin_loader

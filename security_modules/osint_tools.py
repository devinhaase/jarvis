"""
security_modules/osint_tools.py — Passive/light-active recon and OSINT tools, expanding
the Security specialist's Offense pillar beyond scan_ports/run_recon_pipeline.

Every function here names a specific external target (a domain, host, or URL) and is
gated by AuthorizationCheck exactly like scan_ports/shodan_lookup already are — including
tools whose *intent* is defensive (auditing your own site's security headers is still
"reaching a target outside this machine", so it goes through the same gate). See
auth_check.py and data/authorized_targets.json. Nothing here executes an exploit or sends
anything beyond a standard, unauthenticated read (DNS query, TLS handshake, HTTP GET,
public certificate-transparency-log query) — nothing a browser or `curl` wouldn't also do.
"""

import os
import sys
import socket
import ssl
import ipaddress
import subprocess
from datetime import datetime, timezone
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from auth_check import AuthorizationCheck, AuthorizationError

_auth = AuthorizationCheck()


def _hostname_from_url(url: str) -> str:
    """Extract a bare hostname from a URL (or pass a bare host through unchanged) — the
    authorized_targets.json allowlist stores hosts/domains, not full URLs with paths."""
    parsed = urlparse(url if "://" in url else f"//{url}")
    return parsed.hostname or url


# ---------------------------------------------------------------------------
# DNS RECON
# ---------------------------------------------------------------------------

_DNS_RECORD_TYPES = ("A", "AAAA", "MX", "TXT", "NS", "CNAME")


def dns_recon(domain: str) -> dict:
    """
    Resolve common DNS record types for an authorized domain via the system's nslookup
    (no new dependency, no third-party DNS-over-HTTPS service involved — uses whatever
    resolver this machine is already configured to use).
    """
    _auth.is_authorized(domain)

    result = {"domain": domain, "records": {}}
    for rtype in _DNS_RECORD_TYPES:
        try:
            out = subprocess.check_output(
                ["nslookup", "-type=" + rtype, domain],
                text=True, timeout=10, stderr=subprocess.STDOUT
            )
            result["records"][rtype] = _parse_nslookup(out, rtype)
        except FileNotFoundError:
            return {"domain": domain, "error": "nslookup not found on this system (unusual — it ships with Windows)."}
        except subprocess.TimeoutExpired:
            result["records"][rtype] = {"error": "timed out"}
        except subprocess.CalledProcessError as e:
            result["records"][rtype] = {"error": e.output.strip()[:200]}
    return result


def _parse_nslookup(raw: str, rtype: str) -> list:
    """nslookup's plain-text output isn't structured — pull out the lines that look like
    actual answers and drop the resolver-server preamble, rather than parsing exhaustively."""
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    answers = []
    for line in lines:
        low = line.lower()
        if low.startswith(("server:", "address:", "*** ")) and "answer" not in low:
            # "Address:" appears both for the resolver server (skip) and for actual A
            # record answers (keep) — the resolver server line is always in the preamble
            # before any "Name:" line has appeared.
            if not answers and rtype in ("A", "AAAA"):
                continue
        if any(marker in line for marker in ("=", ":")) and not low.startswith("default server"):
            answers.append(line)
    return answers[-10:]  # keep it bounded — nslookup can be chatty


# ---------------------------------------------------------------------------
# SUBDOMAIN ENUMERATION (passive — certificate transparency logs only)
# ---------------------------------------------------------------------------

def subdomain_enum(domain: str) -> dict:
    """
    Passive subdomain enumeration via crt.sh (public Certificate Transparency log search).
    Nothing is sent to the target itself — this only reads a public, third-party log of
    certificates that have already been issued for the domain.
    """
    _auth.is_authorized(domain)

    try:
        import requests
        r = requests.get(
            "https://crt.sh/", params={"q": f"%.{domain}", "output": "json"},
            timeout=20, headers={"User-Agent": "JarvisAgent/1.0"}
        )
        r.raise_for_status()
        entries = r.json()
    except Exception as e:
        return {"domain": domain, "error": f"crt.sh lookup failed: {e}", "subdomains": []}

    names = set()
    for entry in entries:
        for name in entry.get("name_value", "").split("\n"):
            name = name.strip().lower()
            if name and not name.startswith("*.") and name.endswith(domain):
                names.add(name)
            elif name.startswith("*."):
                names.add(name[2:])

    sorted_names = sorted(names)
    return {"domain": domain, "count": len(sorted_names), "subdomains": sorted_names,
            "source": "crt.sh certificate transparency logs (passive)"}


# ---------------------------------------------------------------------------
# WHOIS
# ---------------------------------------------------------------------------

def whois_lookup(target: str) -> dict:
    """WHOIS registration lookup via the system 'whois' client. Not installed by default
    on Windows — degrades gracefully with an install pointer, same convention as nmap."""
    _auth.is_authorized(target)

    try:
        out = subprocess.check_output(["whois", target], text=True, timeout=15, stderr=subprocess.STDOUT)
        return {"target": target, "raw": out.strip()[:4000]}
    except FileNotFoundError:
        return {
            "target": target,
            "error": "whois client not found. Windows: install via 'winget install --id Microsoft.Sysinternals.Whois' "
                     "or download whois.exe from Sysinternals: https://learn.microsoft.com/sysinternals/downloads/whois"
        }
    except subprocess.TimeoutExpired:
        return {"target": target, "error": "whois lookup timed out after 15s"}
    except subprocess.CalledProcessError as e:
        return {"target": target, "error": e.output.strip()[:500]}


# ---------------------------------------------------------------------------
# TECH FINGERPRINTING
# ---------------------------------------------------------------------------

_TECH_SIGNATURES = {
    "WordPress": ("wp-content", "wp-includes", 'name="generator" content="wordpress'),
    "Drupal": ("drupal.js", "/sites/default/files", "x-generator: drupal"),
    "Joomla": ("/media/jui/", 'name="generator" content="joomla'),
    "React": ("__next", "react-root", "data-reactroot"),
    "Vue.js": ("__vue__", "data-v-"),
    "Angular": ("ng-version", "ng-app"),
    "jQuery": ("jquery.min.js", "jquery.js"),
    "Bootstrap": ("bootstrap.min.css", "bootstrap.css"),
    "Nginx": ("server: nginx",),
    "Apache": ("server: apache",),
    "IIS": ("server: microsoft-iis",),
    "Cloudflare": ("server: cloudflare", "cf-ray:"),
    "PHP": ("x-powered-by: php",),
    "ASP.NET": ("x-powered-by: asp.net", "x-aspnet-version:"),
    "Express (Node.js)": ("x-powered-by: express",),
}


def tech_fingerprint(url: str) -> dict:
    """
    Passive tech-stack fingerprinting: one GET request, then look at response headers and
    a few well-known markers in the HTML for common frameworks/servers/CMSes — the same
    kind of thing a browser's dev tools or a Wappalyzer extension shows, not active probing.
    """
    host = _hostname_from_url(url)
    _auth.is_authorized(host)

    try:
        import requests
        full_url = url if "://" in url else f"https://{url}"
        r = requests.get(full_url, timeout=15, headers={"User-Agent": "Mozilla/5.0 (JarvisAgent/1.0)"})
    except Exception as e:
        return {"url": url, "error": f"request failed: {e}"}

    headers_lower = "\n".join(f"{k.lower()}: {v.lower()}" for k, v in r.headers.items())
    body_lower = r.text[:20000].lower()
    haystack = headers_lower + "\n" + body_lower

    detected = [name for name, markers in _TECH_SIGNATURES.items() if any(m in haystack for m in markers)]

    return {
        "url": full_url, "status_code": r.status_code,
        "server_header": r.headers.get("Server", ""),
        "powered_by_header": r.headers.get("X-Powered-By", ""),
        "detected_technologies": detected,
        "note": "Passive detection from headers/HTML markers only — not exhaustive.",
    }


# ---------------------------------------------------------------------------
# TLS CERTIFICATE CHECK
# ---------------------------------------------------------------------------

def check_ssl_cert(host: str, port: int = 443) -> dict:
    """TLS certificate details for an authorized host: issuer, subject, validity window,
    days-until-expiry. Standard TLS handshake only (what any browser does on connect)."""
    _auth.is_authorized(host)

    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert()
    except Exception as e:
        return {"host": host, "port": port, "error": str(e)}

    def _name(field):
        return dict(x[0] for x in cert.get(field, []))

    not_after = cert.get("notAfter", "")
    days_left = None
    try:
        expiry = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        days_left = (expiry - datetime.now(timezone.utc)).days
    except Exception:
        pass

    return {
        "host": host, "port": port,
        "subject": _name("subject"), "issuer": _name("issuer"),
        "not_before": cert.get("notBefore"), "not_after": not_after,
        "days_until_expiry": days_left,
        "subject_alt_names": [v for k, v in cert.get("subjectAltName", []) if k == "DNS"],
        "warning": "Certificate expires soon." if isinstance(days_left, int) and days_left < 30 else None,
    }


# ---------------------------------------------------------------------------
# HTTP SECURITY HEADERS AUDIT
# ---------------------------------------------------------------------------

_SECURITY_HEADERS = {
    "Strict-Transport-Security": "Forces HTTPS on future visits (HSTS).",
    "Content-Security-Policy": "Restricts what content sources the page can load/execute.",
    "X-Frame-Options": "Prevents clickjacking via iframe embedding.",
    "X-Content-Type-Options": "Stops MIME-sniffing away from the declared Content-Type.",
    "Referrer-Policy": "Controls how much of the URL is leaked via the Referer header.",
    "Permissions-Policy": "Restricts access to browser features (camera, geolocation, ...).",
}


def http_security_headers_audit(url: str) -> dict:
    """Checks an authorized site for the presence of standard defensive HTTP response
    headers — the same headers securityheaders.com or Mozilla Observatory check for.
    Framed as offense-tagged/gated because it still reaches a target outside this
    machine, even though the intent is auditing that target's own hardening."""
    host = _hostname_from_url(url)
    _auth.is_authorized(host)

    try:
        import requests
        full_url = url if "://" in url else f"https://{url}"
        r = requests.get(full_url, timeout=15, headers={"User-Agent": "Mozilla/5.0 (JarvisAgent/1.0)"})
    except Exception as e:
        return {"url": url, "error": f"request failed: {e}"}

    present = {}
    missing = []
    for header, explanation in _SECURITY_HEADERS.items():
        if header in r.headers:
            present[header] = r.headers[header]
        else:
            missing.append({"header": header, "why_it_matters": explanation})

    score = f"{len(present)}/{len(_SECURITY_HEADERS)}"
    return {
        "url": full_url, "status_code": r.status_code,
        "score": score, "present_headers": present, "missing_headers": missing,
    }

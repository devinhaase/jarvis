"""
security_modules/ctf_tools.py — CTF assistant, hash tools, cert study, pentest reporting.
"""

import os
import sys
import re
import json
import subprocess
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.makedirs(os.path.join("data", "pentest_reports"), exist_ok=True)


class ScopeError(Exception):
    """Raised when a tool is called outside its declared authorization scope."""
    pass


VALID_CRACK_SOURCES = ("ctf", "practice")


# ---------------------------------------------------------------------------
# HASH IDENTIFICATION
# ---------------------------------------------------------------------------

def identify_hash(hash_str: str) -> dict:
    """Identify hash type by length and pattern."""
    h = hash_str.strip()

    # bcrypt
    if h.startswith(("$2b$", "$2a$", "$2y$")):
        return {"hash_type": "bcrypt", "confidence": "high", "hashcat_mode": "3200",
                "john_format": "bcrypt", "notes": "bcrypt — slow by design, GPU-resistant"}

    # MD5 crypt
    if h.startswith("$1$"):
        return {"hash_type": "md5crypt", "confidence": "high", "hashcat_mode": "500",
                "john_format": "md5crypt", "notes": "MD5-crypt (Linux shadow)"}

    # SHA-512 crypt
    if h.startswith("$6$"):
        return {"hash_type": "sha512crypt", "confidence": "high", "hashcat_mode": "1800",
                "john_format": "sha512crypt", "notes": "SHA-512 crypt (Linux shadow)"}

    hex_chars = set("0123456789abcdefABCDEF")
    is_hex = all(c in hex_chars for c in h)

    length_map = {
        32:  {"hash_type": "MD5 or NTLM", "hashcat_mode": "0 (MD5) or 1000 (NTLM)",
              "john_format": "raw-md5", "notes": "32 hex chars = MD5 or NTLM. Context determines which."},
        40:  {"hash_type": "SHA-1", "hashcat_mode": "100",
              "john_format": "raw-sha1", "notes": ""},
        56:  {"hash_type": "SHA-224", "hashcat_mode": "1300",
              "john_format": "raw-sha224", "notes": ""},
        64:  {"hash_type": "SHA-256", "hashcat_mode": "1400",
              "john_format": "raw-sha256", "notes": ""},
        96:  {"hash_type": "SHA-384", "hashcat_mode": "10800",
              "john_format": "raw-sha384", "notes": ""},
        128: {"hash_type": "SHA-512", "hashcat_mode": "1700",
              "john_format": "raw-sha512", "notes": ""},
    }

    if is_hex and len(h) in length_map:
        info = length_map[len(h)]
        return {**info, "confidence": "medium",
                "notes": info.get("notes", "") or f"Pure hex, length {len(h)}"}

    # Base64 check
    if re.match(r'^[A-Za-z0-9+/]+=*$', h) and len(h) % 4 == 0:
        return {"hash_type": "Base64-encoded", "confidence": "medium",
                "hashcat_mode": "N/A", "john_format": "N/A",
                "notes": "Decode with: base64 -d or Python base64.b64decode()"}

    return {"hash_type": "Unknown", "confidence": "low",
            "hashcat_mode": "unknown", "john_format": "unknown",
            "notes": f"Length: {len(h)}, is_hex: {is_hex}. Try hash-identifier or haiti."}


# ---------------------------------------------------------------------------
# HASH CRACKING (CTF scope)
# ---------------------------------------------------------------------------

def crack_hash(hash_str: str, source: str, wordlist_path: str = None, mode: str = "auto") -> dict:
    """
    Attempt to crack a hash using hashcat or john.
    EXPLICITLY FOR CTF CHALLENGES AND CERT/PRACTICE LABS — not for cracking live credentials
    pulled from a real account or system, yours or otherwise.

    `source` is required and must be "ctf" or "practice" — the call is refused otherwise.
    This is a self-declared scope tag, not a technical guarantee, but it forces a deliberate
    choice instead of silently running against whatever hash is handed over, and every call
    (accepted or refused) is logged to episodic memory either way.
    """
    if source not in VALID_CRACK_SOURCES:
        raise ScopeError(
            f"crack_hash requires source to be one of {VALID_CRACK_SOURCES} "
            f"(got: {source!r}). This tool is scoped to CTF challenges and practice labs — "
            "it must not be used against hashes pulled from a live account or system."
        )

    h = hash_str.strip()
    id_result = identify_hash(h)
    start = datetime.now()

    # Find wordlist
    common_wordlists = [
        wordlist_path,
        "/usr/share/wordlists/rockyou.txt",
        r"C:\Tools\wordlists\rockyou.txt",
        r"C:\Users\devin\wordlists\rockyou.txt",
        "rockyou.txt",
    ]
    wl = next((w for w in common_wordlists if w and os.path.exists(w)), None)

    if not wl:
        return {
            "cracked": False,
            "error": "No wordlist found. Download rockyou.txt and place it at C:\\Users\\devin\\wordlists\\rockyou.txt",
            "identified_as": id_result["hash_type"],
            "tip": "Use: hashcat -m {mode} '{hash}' rockyou.txt"
        }

    hc_mode = id_result.get("hashcat_mode", "0").split(" ")[0]
    john_fmt = id_result.get("john_format", "")

    # Try hashcat first
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write(h + "\n")
            hash_file = f.name

        result = subprocess.run(
            ["hashcat", "-m", hc_mode, hash_file, wl, "--quiet", "--potfile-disable"],
            capture_output=True, text=True, timeout=120
        )
        elapsed = (datetime.now() - start).seconds

        # Check potfile or stdout for cracked
        combined = result.stdout + result.stderr
        cracked_match = re.search(rf"{re.escape(h)}:(.+)", combined)
        if cracked_match:
            plaintext = cracked_match.group(1).strip()
            os.unlink(hash_file)
            return {"cracked": True, "plaintext": plaintext, "tool_used": "hashcat",
                    "time_seconds": elapsed, "hash_type": id_result["hash_type"]}

        os.unlink(hash_file)
        # Not cracked by hashcat — try john
    except FileNotFoundError:
        pass  # hashcat not found, try john
    except subprocess.TimeoutExpired:
        return {"cracked": False, "error": "hashcat timed out after 120s",
                "tip": "Try a smaller wordlist or rule-based attack"}

    # Try john
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write(h + "\n")
            hash_file = f.name

        fmt_flag = [f"--format={john_fmt}"] if john_fmt else []
        subprocess.run(
            ["john", *fmt_flag, f"--wordlist={wl}", hash_file],
            capture_output=True, text=True, timeout=120
        )
        show = subprocess.run(
            ["john", "--show", hash_file],
            capture_output=True, text=True, timeout=10
        )
        elapsed = (datetime.now() - start).seconds

        if ":" in show.stdout and "0 password hashes cracked" not in show.stdout:
            plaintext = show.stdout.split(":")[1].strip()
            os.unlink(hash_file)
            return {"cracked": True, "plaintext": plaintext, "tool_used": "john",
                    "time_seconds": elapsed, "hash_type": id_result["hash_type"]}

        os.unlink(hash_file)
        return {"cracked": False, "hash_type": id_result["hash_type"],
                "note": "Not found in wordlist. Try rules: hashcat -r rules/best64.rule",
                "time_seconds": elapsed}

    except FileNotFoundError:
        return {
            "cracked": False,
            "error": "Neither hashcat nor john is installed.",
            "install": "Windows: https://hashcat.net/hashcat/ and https://www.openwall.com/john/",
            "identified_as": id_result["hash_type"],
            "manual_command": f"hashcat -m {hc_mode} '{h}' rockyou.txt"
        }


# ---------------------------------------------------------------------------
# CTF ASSISTANT
# ---------------------------------------------------------------------------

def ctf_assistant(challenge_description: str) -> str:
    """
    Analyze a CTF challenge and return structured context for the LLM.
    The LLM provides the actual analysis using this as framing.
    """
    categories = {
        "web": ["sql", "xss", "injection", "lfi", "rfi", "ssti", "cookie", "header",
                "http", "php", "jwt", "csrf", "idor", "ssrf", "xpath"],
        "crypto": ["cipher", "encrypt", "decrypt", "rsa", "aes", "base64", "hash",
                   "xor", "rot", "caesar", "vigenere", "otp", "ecdsa"],
        "forensics": ["pcap", "wireshark", "image", "steganography", "metadata",
                      "memory", "dump", "volatility", "file carving", "exif"],
        "pwn": ["buffer overflow", "bof", "rop", "shellcode", "stack", "heap",
                "format string", "ret2", "libc", "pwntools"],
        "reversing": ["reverse", "assembly", "disassemble", "ghidra", "ida", "binary",
                      "elf", "pe", "decompile", "obfuscate"],
        "osint": ["username", "social media", "geolocation", "domain", "whois",
                  "wayback", "shodan", "instagram", "twitter"],
    }

    desc_lower = challenge_description.lower()
    detected = []
    for cat, keywords in categories.items():
        if any(kw in desc_lower for kw in keywords):
            detected.append(cat)

    if not detected:
        detected = ["unknown — requires manual triage"]

    tool_map = {
        "web": ["Burp Suite", "curl", "ffuf/gobuster (directory brute)", "sqlmap (authorized use)", "wfuzz"],
        "crypto": ["CyberChef", "Python cryptography library", "RsaCtfTool", "hashcat (for hashes)"],
        "forensics": ["Wireshark", "Autopsy", "Volatility3", "binwalk", "exiftool", "strings", "file"],
        "pwn": ["pwntools", "GDB + peda/gef/pwndbg", "ROPgadget", "checksec", "strace"],
        "reversing": ["Ghidra", "IDA Free", "Radare2", "strings", "ltrace/strace", "objdump"],
        "osint": ["theHarvester", "Maltego", "Sherlock", "Google Dorks", "Wayback Machine"],
    }

    tools = []
    for cat in detected:
        tools.extend(tool_map.get(cat, []))

    return (
        f"CTF CHALLENGE ANALYSIS REQUEST\n"
        f"Challenge: {challenge_description}\n"
        f"Detected categories: {', '.join(detected)}\n"
        f"Recommended tools: {', '.join(tools) or 'Determine from context'}\n\n"
        "Provide: 1) Likely challenge type and approach, "
        "2) Step-by-step methodology, "
        "3) Common gotchas for this category, "
        "4) Example commands or code snippets where helpful. "
        "Be specific and technical — this is for someone actively solving a CTF."
    )


# ---------------------------------------------------------------------------
# CERT STUDY MODE
# ---------------------------------------------------------------------------

def cert_study_session(cert: str, topic: str = None) -> str:
    """Generate a study context string for a security certification."""
    certs = {
        "ceh": "Certified Ethical Hacker (CEH) by EC-Council",
        "oscp": "Offensive Security Certified Professional (OSCP)",
        "security+": "CompTIA Security+",
        "pentest+": "CompTIA PenTest+",
        "cissp": "Certified Information Systems Security Professional (CISSP)",
        "ejpt": "eLearnSecurity Junior Penetration Tester (eJPT)",
    }
    cert_name = certs.get(cert.lower().replace(" ", ""), cert)

    topic_str = f" — topic: {topic}" if topic else ""
    return (
        f"CERT STUDY SESSION: {cert_name}{topic_str}\n\n"
        "Provide in-depth study material including:\n"
        "1. Key concepts and definitions\n"
        "2. How this appears on the actual exam (question styles, trick answers)\n"
        "3. Hands-on technique explanation with examples\n"
        "4. 3 practice exam questions with explanations\n"
        "5. Common misconceptions to avoid\n"
        "Be technical and specific — treat the user as someone actively preparing for this cert."
    )


# ---------------------------------------------------------------------------
# PENTEST REPORT GENERATOR
# ---------------------------------------------------------------------------

def generate_pentest_report(target: str, recon_results: dict = None,
                            findings: list = None) -> str:
    """
    Generate a professional pentest report in markdown.
    Saves to data/pentest_reports/{target}_{timestamp}.md
    Returns the filepath.
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_target = re.sub(r'[^\w\-.]', '_', target)
    filepath = os.path.join("data", "pentest_reports", f"{safe_target}_{ts}.md")

    recon = recon_results or {}
    findings = findings or []

    # Severity ordering
    sev_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
    findings_sorted = sorted(findings, key=lambda f: sev_order.get(f.get("severity", "INFO"), 4))

    open_ports = recon.get("open_ports", [])
    cve_total = sum(len(p.get("cves", [])) for p in open_ports)

    lines = [
        f"# Penetration Test Report",
        f"",
        f"| Field | Value |",
        f"|---|---|",
        f"| **Target** | `{target}` |",
        f"| **Date** | {datetime.now().strftime('%Y-%m-%d')} |",
        f"| **Scope** | Authorized targets in data/authorized_targets.json |",
        f"| **Methodology** | Recon → Enumeration → Vulnerability Identification → Reporting |",
        f"",
        f"---",
        f"",
        f"## Executive Summary",
        f"",
        f"Host `{target}` was assessed with {len(open_ports)} open ports discovered.",
        f"A total of {cve_total} potential CVE matches were identified during automated scanning.",
        f"{len(findings)} manual findings are documented below.",
        f"",
        f"---",
        f"",
        f"## Attack Surface",
        f"",
        f"| Port | Service | Version | CVEs Found |",
        f"|---|---|---|---|",
    ]

    for p in open_ports:
        cves = len(p.get("cves", []))
        cve_str = f"{cves} CVEs" if cves else "None"
        lines.append(f"| {p.get('port')} | {p.get('service')} | {p.get('version', 'unknown')} | {cve_str} |")

    lines += [
        f"",
        f"---",
        f"",
        f"## Findings",
        f"",
    ]

    if findings_sorted:
        lines += [
            f"| # | Finding | Severity | Recommendation |",
            f"|---|---|---|---|",
        ]
        for i, f in enumerate(findings_sorted, 1):
            lines.append(
                f"| {i} | {f.get('name', 'Unnamed')} | **{f.get('severity', 'INFO')}** "
                f"| {f.get('recommendation', 'Review and remediate')} |"
            )
    else:
        lines.append("_No manual findings documented. Add findings as a list of dicts with: name, severity, description, recommendation._")

    lines += [
        f"",
        f"---",
        f"",
        f"## Remediation Roadmap",
        f"",
        f"1. **Immediate (24–48h):** Address all CRITICAL and HIGH severity findings.",
        f"2. **Short-term (1–2 weeks):** Patch MEDIUM severity issues and update all services to latest versions.",
        f"3. **Ongoing:** Review LOW/INFO findings, implement continuous monitoring.",
        f"",
        f"---",
        f"_Report generated by Jarvis on {datetime.now().isoformat()}_",
    ]

    with open(filepath, 'w') as file:
        file.write("\n".join(lines))

    return filepath


TOOLS = {}

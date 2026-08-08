from enum import Enum
import time

class Tier(Enum):
    TIER_1 = 1  # Read-only
    TIER_2 = 2  # Reversible/Logged
    TIER_3 = 3  # Notify-then-act
    TIER_4 = 4  # Confirmation required

class Tool:
    def __init__(self, name, description, tier, func, role=None):
        self.name = name
        self.description = description
        self.tier = tier
        self.func = func
        # role: "offense" | "defense" | "practice" | None (Phase 3e Security specialist
        # taxonomy). Only offense matters functionally right now — Coordinator gates any
        # offense-tagged tool with a 'target' arg against AuthorizationCheck at dispatch
        # time, on top of whatever that tool already checks itself. defense/practice are
        # informational tags for now (docs, future routing), not enforcement points.
        self.role = role

    def execute(self, *args, **kwargs):
        return self.func(*args, **kwargs)

import os
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# --- Real Assistant Tools ---
def read_recent_emails(max_results=5):
    try:
        if not os.path.exists('token.json'):
            return "Error: token.json not found. Please authenticate first."
            
        creds = Credentials.from_authorized_user_file('token.json', ['https://www.googleapis.com/auth/gmail.readonly'])
        service = build('gmail', 'v1', credentials=creds)
        
        results = service.users().messages().list(userId='me', labelIds=['INBOX', 'UNREAD'], maxResults=max_results).execute()
        messages = results.get('messages', [])

        if not messages:
            return "No unread emails found."
            
        email_data = []
        for msg in messages:
            msg_id = msg['id']
            message = service.users().messages().get(userId='me', id=msg_id, format='metadata', metadataHeaders=['Subject', 'From']).execute()
            
            headers = message['payload'].get('headers', [])
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'No Subject')
            sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown Sender')
            snippet = message.get('snippet', '')
            
            email_data.append({"sender": sender, "subject": subject, "snippet": snippet})
            
        return email_data
    except Exception as e:
        return f"Failed to read emails: {str(e)}"

def mock_draft_note(filename, content):
    with open(f"{filename}.md", 'w') as f:
        f.write(content)
    return f"Draft saved to {filename}.md"

import subprocess

# --- Real Ops Tools ---
def check_system_health():
    try:
        cpu_out = subprocess.check_output(["powershell", "-Command", "@(Get-CimInstance Win32_Processor).LoadPercentage"], text=True).strip()
        return {"status": "ok", "cpu_usage": f"{cpu_out}%"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def scan_local_logs(timeframe_minutes=60):
    try:
        # Get up to 5 recent Errors (Level 2) or Warnings (Level 3) from System/App logs
        cmd = f"Get-WinEvent -FilterHashtable @{{LogName='System','Application'; Level=2,3; StartTime=(Get-Date).AddMinutes(-{timeframe_minutes})}} -MaxEvents 5 -ErrorAction SilentlyContinue | Select-Object TimeCreated, Message | Format-Table -HideTableHeaders"
        out = subprocess.check_output(["powershell", "-Command", cmd], text=True).strip()
        if not out:
            return ["No recent errors or warnings found."]
        return [line.strip() for line in out.split('\n') if line.strip()]
    except Exception as e:
        return [f"Failed to read logs: {str(e)}"]

def run_local_script(command, args=""):
    try:
        full_cmd = f"{command} {args}".strip()
        out = subprocess.check_output(["powershell", "-Command", full_cmd], text=True, stderr=subprocess.STDOUT)
        return out.strip() if out.strip() else "Command executed successfully with no output."
    except subprocess.CalledProcessError as e:
        return f"Command failed with exit code {e.returncode}\nOutput: {e.output}"

def forced_error_tool():
    raise Exception("Simulated connection timeout to log server")

# Register tools
assistant_tools = {
    "read_recent_emails": Tool("read_recent_emails", "Fetch unread emails via Gmail API", Tier.TIER_1, read_recent_emails),
    "draft_local_note": Tool("draft_local_note", "Draft a markdown note", Tier.TIER_2, mock_draft_note)
}

try:
    from web_search import web_search as _web_search

    assistant_tools["web_search"] = Tool(
        "web_search",
        "General-purpose web search (not security-scoped) — DuckDuckGo by default, "
        "no API key required; uses Brave Search if BRAVE_SEARCH_API_KEY is set",
        Tier.TIER_1, _web_search
    )
except Exception:
    pass  # Web search unavailable — Jarvis continues without it

try:
    from task_manager import add_task, list_tasks, complete_task, delete_task

    assistant_tools["add_task"] = Tool("add_task", "Add a task/reminder (title, optional details, optional due_date as YYYY-MM-DD)", Tier.TIER_2, add_task)
    assistant_tools["list_tasks"] = Tool("list_tasks", "List tasks, soonest-due first — undone tasks by default, pass include_completed=true for everything", Tier.TIER_1, list_tasks)
    assistant_tools["complete_task"] = Tool("complete_task", "Mark a task complete by its id", Tier.TIER_2, complete_task)
    assistant_tools["delete_task"] = Tool("delete_task", "Delete a task by its id", Tier.TIER_2, delete_task)
except Exception:
    pass  # Task manager unavailable — Jarvis continues without it

ops_tools = {
    "check_system_health": Tool("check_system_health", "Check system CPU usage", Tier.TIER_1, check_system_health),
    "scan_local_logs": Tool("scan_local_logs", "Scan Windows event logs for errors", Tier.TIER_1, scan_local_logs),
    "run_local_script": Tool("run_local_script", "Run an arbitrary local script or command", Tier.TIER_3, run_local_script),
    "error_tool": Tool("error_tool", "Forces an error for testing", Tier.TIER_1, forced_error_tool)
}

try:
    from conversation_store import semantic_search_conversations

    ops_tools["semantic_search_conversations"] = Tool(
        "semantic_search_conversations",
        "Search past conversations by meaning, not just keyword (complements the GUI's "
        "keyword search) — finds conceptually related conversations even if the wording "
        "differs entirely. Degrades gracefully if the local embedding model isn't available.",
        Tier.TIER_1, semantic_search_conversations
    )
except Exception:
    pass  # Semantic search unavailable — Jarvis continues without it

try:
    from usage_tracker import get_usage_stats

    ops_tools["get_usage_stats"] = Tool(
        "get_usage_stats",
        "Approximate LLM token usage and cost over the last N days (default 30), broken "
        "down by provider — always $0 on Ollama (local), only matters if a paid API is active",
        Tier.TIER_1, get_usage_stats
    )
except Exception:
    pass  # Usage tracking unavailable — Jarvis continues without it

try:
    from backup import create_backup as _create_backup

    ops_tools["create_backup"] = Tool(
        "create_backup",
        "Create a local timestamped zip backup of data/ (conversations, memory, authorized "
        "targets) plus .env/devices.json/credentials — never uploaded anywhere. Prunes old backups.",
        Tier.TIER_2, _create_backup
    )
except Exception:
    pass  # Backup tool unavailable — Jarvis continues without it

try:
    from file_tools import read_local_file, list_directory, write_local_file, move_or_rename_path

    ops_tools["read_local_file"] = Tool("read_local_file", "Read a local text file's content (truncated to max_chars)", Tier.TIER_1, read_local_file)
    ops_tools["list_directory"] = Tool("list_directory", "List a local directory's immediate contents (name, is_dir, size_bytes)", Tier.TIER_1, list_directory)
    ops_tools["write_local_file"] = Tool("write_local_file", "Write or append to a local text file", Tier.TIER_2, write_local_file)
    ops_tools["move_or_rename_path"] = Tool("move_or_rename_path", "Move or rename a local file/directory — refuses if the destination already exists", Tier.TIER_3, move_or_rename_path)
except Exception:
    pass  # File tools unavailable — Jarvis continues without them

ALL_TOOLS = {**assistant_tools, **ops_tools}

# --- Security Tools (registered separately; require authorized_targets.json) ---
try:
    from security_tools import (
        scan_ports, get_security_posture, check_password_breach,
        check_email_breach, get_security_reference, check_credential_hygiene
    )
    from auth_check import AuthorizationError

    security_tools = {
        "scan_ports": Tool("scan_ports", "Scan open ports on an authorized host (nmap required)", Tier.TIER_3, scan_ports, role="offense"),
        "get_security_posture": Tool("get_security_posture", "Check local machine security posture: Defender, firewall, patches, listening services", Tier.TIER_1, get_security_posture, role="defense"),
        "check_password_breach": Tool("check_password_breach", "Check if a password has appeared in known breaches via k-anonymity API (password never sent)", Tier.TIER_1, check_password_breach, role="defense"),
        "check_email_breach": Tool("check_email_breach", "Check if an email address appears in known data breaches via HaveIBeenPwned API", Tier.TIER_1, check_email_breach, role="defense"),
        "get_security_reference": Tool("get_security_reference", "Get educational reference on ethical hacking, CTF, or security certification topics", Tier.TIER_1, get_security_reference, role="practice"),
        "check_credential_hygiene": Tool("check_credential_hygiene", "Analyze your local Bitwarden vault for reused/breached passwords via the bw CLI (requires BW_SESSION; Jarvis never touches your master password)", Tier.TIER_1, check_credential_hygiene, role="defense"),
    }
    ALL_TOOLS = {**ALL_TOOLS, **security_tools}
except Exception as _sec_err:
    pass  # Security tools unavailable — Jarvis continues without them

# --- Recon Tools (authorized targets only; passive lookups still gated) ---
try:
    from security_modules.recon_tools import (
        lookup_cve, search_exploitdb, run_recon_pipeline,
        shodan_lookup, run_privesc_enum
    )

    recon_tools = {
        "lookup_cve": Tool("lookup_cve", "Search NIST NVD for CVEs matching a service/version string (public API, read-only)", Tier.TIER_1, lookup_cve, role="practice"),
        "search_exploitdb": Tool("search_exploitdb", "Search Exploit-DB for public exploits matching a search term (public API, read-only, no exploit execution)", Tier.TIER_1, search_exploitdb, role="practice"),
        "run_recon_pipeline": Tool("run_recon_pipeline", "Full recon pipeline (host discovery -> port scan -> service ID -> CVE lookup) against an authorized target only", Tier.TIER_3, run_recon_pipeline, role="offense"),
        "shodan_lookup": Tool("shodan_lookup", "Look up an authorized IP/domain on Shodan (passive, requires SHODAN_API_KEY, still gated by authorized_targets.json)", Tier.TIER_1, shodan_lookup, role="offense"),
        "run_privesc_enum": Tool("run_privesc_enum", "Run privilege escalation enumeration on the local machine (always authorized, local only)", Tier.TIER_1, run_privesc_enum, role="offense"),
    }
    ALL_TOOLS = {**ALL_TOOLS, **recon_tools}
except Exception as _recon_err:
    pass  # Recon tools unavailable — Jarvis continues without them

# --- CTF / Cert Study Tools (educational; crack_hash requires an explicit source tag) ---
try:
    from security_modules.ctf_tools import (
        identify_hash, crack_hash, ctf_assistant,
        cert_study_session, generate_pentest_report
    )

    ctf_tools = {
        "identify_hash": Tool("identify_hash", "Identify a hash's type from its length/format (no cracking, informational only)", Tier.TIER_1, identify_hash, role="practice"),
        "crack_hash": Tool("crack_hash", "Attempt to crack a hash via hashcat/john for a CTF or practice lab. REQUIRES a 'source' arg of 'ctf' or 'practice' — refuses otherwise.", Tier.TIER_2, crack_hash, role="practice"),
        "ctf_assistant": Tool("ctf_assistant", "Analyze a CTF challenge description and return category + methodology context for the LLM", Tier.TIER_1, ctf_assistant, role="practice"),
        "cert_study_session": Tool("cert_study_session", "Generate a study session context for a security certification (CEH, OSCP, Security+, etc.)", Tier.TIER_1, cert_study_session, role="practice"),
        "generate_pentest_report": Tool("generate_pentest_report", "Generate a markdown pentest report from recon results/findings, saved to data/pentest_reports/", Tier.TIER_2, generate_pentest_report, role="practice"),
    }
    ALL_TOOLS = {**ALL_TOOLS, **ctf_tools}
except Exception as _ctf_err:
    pass  # CTF tools unavailable — Jarvis continues without them

# --- OSINT/Recon Tools (authorized targets only; every one is gated, offense-intent or not) ---
try:
    from security_modules.osint_tools import (
        dns_recon, subdomain_enum, whois_lookup, tech_fingerprint,
        check_ssl_cert, http_security_headers_audit,
    )

    osint_tools = {
        "dns_recon": Tool("dns_recon", "Resolve A/AAAA/MX/TXT/NS/CNAME records for an authorized domain via nslookup", Tier.TIER_1, dns_recon, role="offense"),
        "subdomain_enum": Tool("subdomain_enum", "Passively enumerate subdomains for an authorized domain via public certificate-transparency logs (crt.sh) — nothing sent to the target itself", Tier.TIER_1, subdomain_enum, role="offense"),
        "whois_lookup": Tool("whois_lookup", "WHOIS registration lookup for an authorized domain/IP (requires a local whois client)", Tier.TIER_1, whois_lookup, role="offense"),
        "tech_fingerprint": Tool("tech_fingerprint", "Passively fingerprint the tech stack (server, CMS, JS frameworks) of an authorized URL from one GET request", Tier.TIER_1, tech_fingerprint, role="offense"),
        "check_ssl_cert": Tool("check_ssl_cert", "Check an authorized host's TLS certificate: issuer, validity window, days until expiry", Tier.TIER_1, check_ssl_cert, role="offense"),
        "http_security_headers_audit": Tool("http_security_headers_audit", "Audit an authorized URL for standard defensive HTTP headers (HSTS, CSP, X-Frame-Options, ...) — same checks as securityheaders.com", Tier.TIER_1, http_security_headers_audit, role="offense"),
    }
    ALL_TOOLS = {**ALL_TOOLS, **osint_tools}
except Exception as _osint_err:
    pass  # OSINT tools unavailable — Jarvis continues without them

# --- Security Reference Tools (static curated content; exploitdb_usage fetches one public page) ---
try:
    from security_modules.reference_tools import (
        mitre_attack_lookup, owasp_top10_reference, exploitdb_usage, reverse_shell_cheatsheet,
    )

    reference_tools = {
        "mitre_attack_lookup": Tool("mitre_attack_lookup", "Look up a MITRE ATT&CK tactic or technique by name, ID, or keyword (curated reference set)", Tier.TIER_1, mitre_attack_lookup, role="practice"),
        "owasp_top10_reference": Tool("owasp_top10_reference", "Look up an OWASP Top 10 (2021) category — example, impact, mitigation. No topic returns all ten.", Tier.TIER_1, owasp_top10_reference, role="practice"),
        "exploitdb_usage": Tool("exploitdb_usage", "Fetch the published usage notes/source for a specific Exploit-DB entry by ID — read-only, same public content search_exploitdb links to", Tier.TIER_1, exploitdb_usage, role="practice"),
        "reverse_shell_cheatsheet": Tool("reverse_shell_cheatsheet", "Static reference: common reverse-shell one-liners (bash/python/perl/php/powershell/nc/socat), filled in with your listener's LHOST/LPORT if given", Tier.TIER_1, reverse_shell_cheatsheet, role="practice"),
    }
    ALL_TOOLS = {**ALL_TOOLS, **reference_tools}
except Exception as _ref_err:
    pass  # Reference tools unavailable — Jarvis continues without them

# --- Payload Generation & Exploit Execution (Tier 4 — explicit confirmation every call) ---
# The most consequential tools in this codebase. generate_payload only ever writes a file
# (never executes/deploys it); run_exploit_module runs exactly one named module, one shot,
# with no session persistence or chaining — see security_modules/exploit_tools.py's module
# docstring for the full scope agreement this was built against. Both require the
# Metasploit Framework installed (msfvenom/msfconsole) and degrade gracefully without it.
try:
    from security_modules.exploit_tools import generate_payload, run_exploit_module, msf_module_info

    exploit_tools_reg = {
        "generate_payload": Tool("generate_payload", "Generate an attack payload via msfvenom for an authorized target — writes a file only, never executes/deploys it (Tier 4: requires explicit confirmation)", Tier.TIER_4, generate_payload, role="offense"),
        "run_exploit_module": Tool("run_exploit_module", "Run one exact, named Metasploit exploit module against an authorized target, one shot, no session chaining (Tier 4: requires explicit confirmation)", Tier.TIER_4, run_exploit_module, role="offense"),
        "msf_module_info": Tool("msf_module_info", "Read-only: search Metasploit modules matching a term and return the top match's documentation — no module is run", Tier.TIER_1, msf_module_info, role="practice"),
    }
    ALL_TOOLS = {**ALL_TOOLS, **exploit_tools_reg}
except Exception as _exploit_err:
    pass  # Exploit tools unavailable — Jarvis continues without them

# --- Dependency Vulnerability Audit (defense — your own code, not a remote target) ---
try:
    from security_modules.dependency_audit import check_dependency_vulnerabilities

    dependency_tools = {
        "check_dependency_vulnerabilities": Tool("check_dependency_vulnerabilities", "Check a local requirements.txt's exactly-pinned packages against known CVEs via OSV.dev (defaults to this project's own requirements.txt)", Tier.TIER_1, check_dependency_vulnerabilities, role="defense"),
    }
    ALL_TOOLS = {**ALL_TOOLS, **dependency_tools}
except Exception as _dep_err:
    pass  # Dependency audit tool unavailable — Jarvis continues without it

# ---------------------------------------------------------------------------
# CAPABILITY REQUIREMENTS (multi-device routing)
# ---------------------------------------------------------------------------
# Tool execution always happens wherever the Coordinator/ALL_TOOLS instance is running —
# in the multi-device server (Phase 2d), that's the server host, not whichever device's
# session requested it. This map isn't "which machine the tool touches" in the abstract —
# it's "does this tool reach outside the conversation into the machine or local network the
# brain is running on", which is exactly the class of action a connecting session shouldn't
# get to trigger unless its device declared it's allowed to: the desktop client declares
# "filesystem" for itself; a future mobile client, talking to the same server, would not,
# and these tools get refused for it rather than silently running against your desktop.
# Tools not listed here are unrestricted (read-only / cloud-API calls, location-agnostic).
REQUIRES_CAPABILITY = {
    "draft_local_note": "filesystem",
    "check_system_health": "filesystem",
    "scan_local_logs": "filesystem",
    "run_local_script": "filesystem",
    "get_security_posture": "filesystem",
    "scan_ports": "filesystem",
    "run_recon_pipeline": "filesystem",
    "run_privesc_enum": "filesystem",
    "crack_hash": "filesystem",
    "generate_pentest_report": "filesystem",
    "check_credential_hygiene": "filesystem",
    "dns_recon": "filesystem",              # shells out to nslookup
    "whois_lookup": "filesystem",           # shells out to whois
    "check_ssl_cert": "filesystem",         # raw socket connect from wherever the server runs
    "check_dependency_vulnerabilities": "filesystem",  # reads a local requirements file
    "generate_payload": "filesystem",       # shells out to msfvenom, writes a local file
    "run_exploit_module": "filesystem",     # shells out to msfconsole, runs against the network
    "msf_module_info": "filesystem",        # shells out to msfconsole (read-only, but still local subprocess)
    "create_backup": "filesystem",          # reads/writes local files
    "read_local_file": "filesystem",
    "list_directory": "filesystem",
    "write_local_file": "filesystem",
    "move_or_rename_path": "filesystem",
}

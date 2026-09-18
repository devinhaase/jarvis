from enum import Enum
import time

class Tier(Enum):
    TIER_1 = 1  # Read-only
    TIER_2 = 2  # Reversible/Logged
    TIER_3 = 3  # Notify-then-act
    TIER_4 = 4  # Confirmation required

class Tool:
    def __init__(self, name, description, tier, func, role=None, team=None):
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
        # team: "personal_assistant" | "network" | "it" | "cybersecurity" | "hacking" | None
        # (Phase 4 team taxonomy — orthogonal to `role`: role is a security posture tag,
        # team is which functional group owns this capability). None means coordinator-level
        # — always reachable regardless of which team is routed to, currently just the
        # self-knowledge tool (see teams.py's module docstring for why it specifically
        # stays outside any one team's scope).
        self.team = team

    def execute(self, *args, **kwargs):
        return self.func(*args, **kwargs)

import os

# --- Real Assistant Tools ---
def read_recent_emails(max_results=5):
    # Phase 6 item 4: migrated off a direct plain token.json read onto google_auth.py's
    # shared, encrypted credential store — same one google_tools.py's newer Gmail/
    # Calendar/Drive functions use, so there's exactly one Google auth path in this
    # codebase, not two diverging ones.
    try:
        import google_auth
        from googleapiclient.discovery import build

        creds = google_auth.get_credentials()
        if creds is None:
            return "Error: Google account not connected. Run google_auth_setup.py once to connect it."
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
        # Get up to 5 recent Errors (Level 2) or Warnings (Level 3) from System/App logs.
        # Real bug found live (Devin reported a false "access denied" — Jarvis had
        # misread a generic, contentless error and guessed a cause): Get-WinEvent treats
        # "zero events matched the filter" as a terminating error — even under
        # -ErrorAction SilentlyContinue, the pipeline still exits non-zero — and that's a
        # completely normal, good-news outcome ("nothing broke recently"), not a real
        # failure. subprocess.check_output only captures stdout, so on any non-zero exit
        # the actual PowerShell error text (which plainly says "No events were found
        # that match the specified selection criteria") was thrown away entirely, leaving
        # only "returned non-zero exit status 1" for the model to interpret — which it
        # then had to guess an explanation for, and guessed wrong. Fixed by using run()
        # with stderr captured, and checking for that specific benign case by name
        # (NoMatchingEventsFound) before treating anything else as a real error.
        cmd = f"Get-WinEvent -FilterHashtable @{{LogName='System','Application'; Level=2,3; StartTime=(Get-Date).AddMinutes(-{timeframe_minutes})}} -MaxEvents 5 -ErrorAction Stop | Select-Object TimeCreated, Message | Format-Table -HideTableHeaders"
        result = subprocess.run(["powershell", "-Command", cmd], capture_output=True, text=True)
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            if "NoMatchingEventsFound" in stderr or "No events were found" in stderr:
                return ["No recent errors or warnings found."]
            return [f"Failed to read logs: {stderr or 'unknown PowerShell error (exit code %d)' % result.returncode}"]
        out = (result.stdout or "").strip()
        if not out:
            return ["No recent errors or warnings found."]
        return [line.strip() for line in out.split('\n') if line.strip()]
    except Exception as e:
        return [f"Failed to read logs: {str(e)}"]

def run_local_script(command, args=""):
    try:
        from security_hardening import run_hardened
        full_cmd = f"{command} {args}".strip()
        # Hardened (Phase 8): the child gets a minimal env, not this process's full
        # environment (which includes any API keys loaded from .env) — this tool takes
        # free-form, potentially LLM-composed input, the highest-risk shell surface here.
        result = run_hardened(["powershell", "-Command", full_cmd])
        out = ((result.stdout or "") + (result.stderr or "")).strip()
        if result.returncode != 0:
            return f"Command failed with exit code {result.returncode}\nOutput: {out}"
        return out if out else "Command executed successfully with no output."
    except subprocess.TimeoutExpired:
        return "Command timed out after 60 seconds."

def forced_error_tool():
    raise Exception("Simulated connection timeout to log server")

# Register tools
# Phase 4: `team=` tags every tool with which of the 5 subagent teams owns it (see
# teams.py). Orthogonal to `role` — role is a security-posture tag (offense/defense/
# practice), team is a functional-ownership tag (personal_assistant/network/it/
# cybersecurity/hacking). Tools with no team stay coordinator-level, reachable regardless
# of which team is routed to — see teams.py's docstring for exactly which tools and why.
assistant_tools = {
    "read_recent_emails": Tool("read_recent_emails", "Fetch unread emails via Gmail API", Tier.TIER_1, read_recent_emails, team="personal_assistant"),
    "draft_local_note": Tool("draft_local_note", "Draft a markdown note", Tier.TIER_2, mock_draft_note, team="personal_assistant")
}

try:
    from web_search import web_search as _web_search

    assistant_tools["web_search"] = Tool(
        "web_search",
        "General-purpose web search (not security-scoped) — DuckDuckGo by default, "
        "no API key required; uses Brave Search if BRAVE_SEARCH_API_KEY is set",
        Tier.TIER_1, _web_search, team="personal_assistant"
    )
except Exception:
    pass  # Web search unavailable — Jarvis continues without it

try:
    from task_manager import add_task, list_tasks, complete_task, delete_task

    assistant_tools["add_task"] = Tool("add_task", "Add a task/reminder (title, optional details, optional due_date as YYYY-MM-DD)", Tier.TIER_2, add_task, team="personal_assistant")
    assistant_tools["list_tasks"] = Tool("list_tasks", "List tasks, soonest-due first — undone tasks by default, pass include_completed=true for everything", Tier.TIER_1, list_tasks, team="personal_assistant")
    assistant_tools["complete_task"] = Tool("complete_task", "Mark a task complete by its id", Tier.TIER_2, complete_task, team="personal_assistant")
    assistant_tools["delete_task"] = Tool("delete_task", "Delete a task by its id", Tier.TIER_2, delete_task, team="personal_assistant")
except Exception:
    pass  # Task manager unavailable — Jarvis continues without it

try:
    from goals import add_goal, list_goals, check_in_goal, resolve_goal, delete_goal

    assistant_tools["add_goal"] = Tool(
        "add_goal",
        "Add a standing goal (not a one-shot task) that Jarvis periodically asks about "
        "for a status update, e.g. 'keep an eye on Twingate reconnect stability' — title, "
        "optional details, optional check_in_interval_days (default 7)",
        Tier.TIER_2, add_goal, team="personal_assistant",
    )
    assistant_tools["list_goals"] = Tool(
        "list_goals", "List goals, soonest-check-in first — active goals by default, "
        "pass include_resolved=true for achieved/abandoned ones too",
        Tier.TIER_1, list_goals, team="personal_assistant",
    )
    assistant_tools["check_in_goal"] = Tool(
        "check_in_goal", "Log a progress note against a goal by its id and reschedule its "
        "next check-in from now",
        Tier.TIER_2, check_in_goal, team="personal_assistant",
    )
    assistant_tools["resolve_goal"] = Tool(
        "resolve_goal", "Mark a goal 'achieved' or 'abandoned' by its id — stops it from "
        "being surfaced again",
        Tier.TIER_2, resolve_goal, team="personal_assistant",
    )
    assistant_tools["delete_goal"] = Tool("delete_goal", "Delete a goal by its id", Tier.TIER_2, delete_goal, team="personal_assistant")
except Exception:
    pass  # Goals module unavailable — Jarvis continues without it

try:
    from conversation_store import semantic_search_conversations

    assistant_tools["semantic_search_conversations"] = Tool(
        "semantic_search_conversations",
        "Search past conversations by meaning, not just keyword (complements the GUI's "
        "keyword search) — finds conceptually related conversations even if the wording "
        "differs entirely. Degrades gracefully if the local embedding model isn't available.",
        Tier.TIER_1, semantic_search_conversations, team="personal_assistant"
    )
except Exception:
    pass  # Semantic search unavailable — Jarvis continues without it

ops_tools = {
    "check_system_health": Tool("check_system_health", "Check system CPU usage", Tier.TIER_1, check_system_health, team="it"),
    "scan_local_logs": Tool("scan_local_logs", "Scan Windows event logs for errors", Tier.TIER_1, scan_local_logs, team="it"),
    "run_local_script": Tool("run_local_script", "Run an arbitrary local script or command", Tier.TIER_3, run_local_script, team="it"),
    "error_tool": Tool("error_tool", "Forces an error for testing", Tier.TIER_1, forced_error_tool)  # no team — test-only utility, not a real capability
}

try:
    from usage_tracker import get_usage_stats

    ops_tools["get_usage_stats"] = Tool(
        "get_usage_stats",
        "Approximate LLM token usage and cost over the last N days (default 30), broken "
        "down by provider — always $0 on Ollama (local), only matters if a paid API is active",
        Tier.TIER_1, get_usage_stats, team="it"
    )
except Exception:
    pass  # Usage tracking unavailable — Jarvis continues without it

try:
    from backup import create_backup as _create_backup

    ops_tools["create_backup"] = Tool(
        "create_backup",
        "Create a local timestamped zip backup of data/ (conversations, memory, authorized "
        "targets) plus .env/devices.json/credentials — never uploaded anywhere. Prunes old backups.",
        Tier.TIER_2, _create_backup, team="it"
    )
except Exception:
    pass  # Backup tool unavailable — Jarvis continues without it

try:
    from file_tools import read_local_file, list_directory, write_local_file, move_or_rename_path

    ops_tools["read_local_file"] = Tool("read_local_file", "Read a local text file's content (truncated to max_chars)", Tier.TIER_1, read_local_file, team="it")
    ops_tools["list_directory"] = Tool("list_directory", "List a local directory's immediate contents (name, is_dir, size_bytes)", Tier.TIER_1, list_directory, team="it")
    ops_tools["write_local_file"] = Tool("write_local_file", "Write or append to a local text file", Tier.TIER_2, write_local_file, team="it")
    ops_tools["move_or_rename_path"] = Tool("move_or_rename_path", "Move or rename a local file/directory — refuses if the destination already exists", Tier.TIER_3, move_or_rename_path, team="it")
except Exception:
    pass  # File tools unavailable — Jarvis continues without them

try:
    from security_hardening import run_self_audit

    # run_self_audit stays owned by Cybersecurity (it's their posture-check tool).
    # (This block used to also register a file-flag "kill switch" emergency stop;
    # removed at Devin's explicit request — see security_hardening.py's module
    # docstring and task.md's entry for the removal.)
    ops_tools["run_self_audit"] = Tool(
        "run_self_audit",
        "Read-only audit of Jarvis's own security hardening: whether "
        "credential files are accidentally tracked in git, registered device count/staleness.",
        Tier.TIER_1, run_self_audit, role="defense", team="cybersecurity",
    )
except Exception:
    pass  # Hardening tools unavailable — Jarvis continues without them

try:
    from generate_self_knowledge import get_self_knowledge

    # Also coordinator-level — meta info about Jarvis itself isn't any one team's domain.
    ops_tools["get_self_knowledge"] = Tool(
        "get_self_knowledge",
        "Return a live self-knowledge document about Jarvis itself: full tool inventory by "
        "tier/role, current security posture, test coverage, recent capability history, and "
        "known scope boundaries. Call this when Devin asks what you can do, how you're built, "
        "or what your limits are — don't guess from training data.",
        Tier.TIER_1, get_self_knowledge,
    )
except Exception:
    pass  # Self-knowledge doc unavailable — Jarvis continues without it

try:
    from conversation_store import store as _store

    def _create_team_incident(created_by_team, title, description="", target_team=None, severity="info"):
        return _store.create_incident(created_by_team, title, description, target_team, severity)

    def _list_team_incidents(target_team=None, status=None):
        return _store.list_incidents(target_team=target_team, status=status)

    # Coordinator-level (team=None): the shared board isn't any one team's domain — every
    # team can read the queue and file a finding for another team to pick up.
    ops_tools["create_team_incident"] = Tool(
        "create_team_incident",
        "File a finding on the shared team incident board for another team to pick up "
        "(target_team=null broadcasts to any team's queue). This is how a Network or IT "
        "finding reaches the Cybersecurity team without going through Devin each time.",
        Tier.TIER_2, _create_team_incident,
    )
    ops_tools["list_team_incidents"] = Tool(
        "list_team_incidents",
        "List items on the shared team incident board — optionally filtered by target_team "
        "and/or status (open/acknowledged/resolved).",
        Tier.TIER_1, _list_team_incidents,
    )
except Exception:
    pass  # Team board unavailable — Jarvis continues without it

try:
    from obsidian_tools import (
        list_vault_structure, read_note, search_vault, create_note, append_note,
        overwrite_note, delete_note, index_vault_into_memory,
    )

    obsidian_tools_reg = {
        "list_vault_structure": Tool("list_vault_structure", "List your Obsidian vault's folder structure and notes per folder", Tier.TIER_1, list_vault_structure, team="personal_assistant"),
        "read_note": Tool("read_note", "Read a note from your Obsidian vault by its path", Tier.TIER_1, read_note, team="personal_assistant"),
        "search_vault": Tool("search_vault", "Keyword search across every note in your Obsidian vault", Tier.TIER_1, search_vault, team="personal_assistant"),
        "create_note": Tool("create_note", "Create a new note in your Obsidian vault with proper frontmatter/tags — refuses if a note already exists at that path", Tier.TIER_2, create_note, team="personal_assistant"),
        "append_note": Tool("append_note", "Append to an existing note (or today's daily note if no path given, auto-created from the daily template)", Tier.TIER_2, append_note, team="personal_assistant"),
        "overwrite_note": Tool("overwrite_note", "Replace an EXISTING note's entire content — never touches your notes without explicit confirmation (Tier 4)", Tier.TIER_4, overwrite_note, team="personal_assistant"),
        "delete_note": Tool("delete_note", "Delete a note from your vault — never without explicit confirmation (Tier 4)", Tier.TIER_4, delete_note, team="personal_assistant"),
        "index_vault_into_memory": Tool("index_vault_into_memory", "Re-index every vault note into semantic search, so vault knowledge surfaces alongside conversation history", Tier.TIER_2, index_vault_into_memory, team="personal_assistant"),
    }
    assistant_tools = {**assistant_tools, **obsidian_tools_reg}
except Exception as _obsidian_err:
    pass  # Obsidian tools unavailable — Jarvis continues without them

# --- Google integration (Phase 6 item 4) — narrowest OAuth scopes that cover this, see
# google_auth.py's docstring. Sending/deleting/modifying-existing always Tier 4 regardless
# of what the OAuth scope technically permits — the tier gate is the actual enforcement.
try:
    from google_tools import (
        search_emails, read_email, draft_email, send_email,
        list_calendar_events, create_calendar_event, update_calendar_event, delete_calendar_event,
        search_drive_files, read_drive_file, create_drive_file, update_drive_file, delete_drive_file,
        google_connection_status,
    )

    google_tools_reg = {
        "search_emails": Tool("search_emails", "Search Gmail using real Gmail search syntax (e.g. 'from:x is:unread')", Tier.TIER_1, search_emails, team="personal_assistant"),
        "read_email": Tool("read_email", "Read one email's full body by message id", Tier.TIER_1, read_email, team="personal_assistant"),
        "draft_email": Tool("draft_email", "Create a Gmail draft — does NOT send it", Tier.TIER_2, draft_email, team="personal_assistant"),
        "send_email": Tool("send_email", "Send an email (a draft or composed directly) — ALWAYS requires explicit confirmation, never sends autonomously", Tier.TIER_4, send_email, team="personal_assistant"),
        "list_calendar_events": Tool("list_calendar_events", "List upcoming Calendar events", Tier.TIER_1, list_calendar_events, team="personal_assistant"),
        "create_calendar_event": Tool("create_calendar_event", "Create a new Calendar event", Tier.TIER_2, create_calendar_event, team="personal_assistant"),
        "update_calendar_event": Tool("update_calendar_event", "Update an existing Calendar event's details", Tier.TIER_2, update_calendar_event, team="personal_assistant"),
        "delete_calendar_event": Tool("delete_calendar_event", "Delete a Calendar event — requires explicit confirmation every time", Tier.TIER_4, delete_calendar_event, team="personal_assistant"),
        "search_drive_files": Tool("search_drive_files", "Search Drive files this app can see (files it created or you explicitly opened with it — drive.file scope)", Tier.TIER_1, search_drive_files, team="personal_assistant"),
        "read_drive_file": Tool("read_drive_file", "Read a Drive file's content", Tier.TIER_1, read_drive_file, team="personal_assistant"),
        "create_drive_file": Tool("create_drive_file", "Create a new file in Drive", Tier.TIER_2, create_drive_file, team="personal_assistant"),
        "update_drive_file": Tool("update_drive_file", "Replace an EXISTING Drive file's content — requires explicit confirmation every time", Tier.TIER_4, update_drive_file, team="personal_assistant"),
        "delete_drive_file": Tool("delete_drive_file", "Delete a Drive file — requires explicit confirmation every time", Tier.TIER_4, delete_drive_file, team="personal_assistant"),
        "google_connection_status": Tool("google_connection_status", "Check whether Gmail/Calendar/Drive are connected and which scopes are actually granted", Tier.TIER_1, google_connection_status),
    }
    assistant_tools = {**assistant_tools, **google_tools_reg}
except Exception as _google_err:
    pass  # Google tools unavailable — Jarvis continues without them

# --- Push notifications (Phase 6 item 6) — the only tool-facing surface; subscribing,
# category toggles, and quiet hours are device settings, not something a conversation turn
# should change, so they live entirely in server.py's WS handlers, not here.
try:
    from push_notifications import send_to_all as _push_send_to_all

    def _send_test_push(message="Test notification from Jarvis"):
        sent = _push_send_to_all("messages", "Jarvis: test notification", message, tag="test")
        return f"Sent to {sent} subscribed device(s)." if sent else (
            "No subscribed device received it — either nothing is subscribed, or the "
            "'messages' category is toggled off, or it's currently within a subscribed "
            "device's quiet hours."
        )

    # Coordinator-level: not any one team's tool, purely a diagnostic for verifying push
    # actually reaches a device end to end.
    ops_tools["send_test_push"] = Tool(
        "send_test_push",
        "Send a test push notification to every device with push notifications enabled, to "
        "verify the connection actually works. Read-only aside from the notification itself "
        "— changes no persisted state.",
        Tier.TIER_1, _send_test_push,
    )
except Exception:
    pass  # Push notifications unavailable — Jarvis continues without them

try:
    from network_tools import (
        ping_sweep, arp_table_snapshot, check_latency, bandwidth_sample,
        check_wan_status, diagnose_connectivity,
    )

    network_tools_reg = {
        "ping_sweep": Tool("ping_sweep", "ICMP ping sweep of the local subnet (auto-detected from the real LAN adapter) — returns which hosts respond", Tier.TIER_1, ping_sweep, team="network"),
        "arp_table_snapshot": Tool("arp_table_snapshot", "Read the local ARP table for a device inventory (IP + MAC pairs) — diffable across calls to spot a new device joining", Tier.TIER_1, arp_table_snapshot, team="network"),
        "check_latency": Tool("check_latency", "Ping a host (default 8.8.8.8) N times and report min/avg/max latency and packet loss", Tier.TIER_1, check_latency, team="network"),
        "bandwidth_sample": Tool("bandwidth_sample", "Sample this machine's network interface bytes sent/received over a short window via psutil, report an approximate throughput", Tier.TIER_1, bandwidth_sample, team="network"),
        "check_wan_status": Tool("check_wan_status", "Query the router's UPnP/IGD service (if enabled) for external IP and WAN byte counters — degrades gracefully if UPnP isn't reachable", Tier.TIER_1, check_wan_status, team="network"),
        "diagnose_connectivity": Tool("diagnose_connectivity", "Combine ping sweep, latency, ARP inventory, and WAN status into a synthesized connectivity diagnosis with suggested next steps (diagnosis only — never changes live config)", Tier.TIER_1, diagnose_connectivity, team="network"),
    }
    ops_tools = {**ops_tools, **network_tools_reg}
except Exception as _net_err:
    pass  # Network tools unavailable — Jarvis continues without them

# --- Uptime Kuma (Devin's own instance, port 3001, this machine) — read-only, no
# credentials, no Twingate gate (co-located with the brain, not a home-network resource
# reached remotely — see uptime_kuma_tools.py's own module docstring for why that
# distinction matters here).
try:
    from uptime_kuma_tools import get_uptime_kuma_status

    uptime_kuma_reg = {
        "get_uptime_kuma_status": Tool("get_uptime_kuma_status", "Check the current up/down status and 24h uptime percentage of every monitor on Devin's Uptime Kuma instance", Tier.TIER_1, get_uptime_kuma_status, team="network"),
    }
    ops_tools = {**ops_tools, **uptime_kuma_reg}
except Exception as _kuma_err:
    pass  # Uptime Kuma tool unavailable — Jarvis continues without it

ALL_TOOLS = {**assistant_tools, **ops_tools}

# --- Security Tools (registered separately; require authorized_targets.json) ---
try:
    from security_tools import (
        scan_ports, get_security_posture, check_password_breach,
        check_email_breach, get_security_reference, check_credential_hygiene
    )
    from auth_check import AuthorizationError

    security_tools = {
        "scan_ports": Tool("scan_ports", "Scan open ports on an authorized host (nmap required)", Tier.TIER_3, scan_ports, role="offense", team="hacking"),
        "get_security_posture": Tool("get_security_posture", "Check local machine security posture: Defender, firewall, patches, listening services", Tier.TIER_1, get_security_posture, role="defense", team="cybersecurity"),
        "check_password_breach": Tool("check_password_breach", "Check if a password has appeared in known breaches via k-anonymity API (password never sent)", Tier.TIER_1, check_password_breach, role="defense", team="cybersecurity"),
        "check_email_breach": Tool("check_email_breach", "Check if an email address appears in known data breaches via HaveIBeenPwned API", Tier.TIER_1, check_email_breach, role="defense", team="cybersecurity"),
        "get_security_reference": Tool("get_security_reference", "Get educational reference on ethical hacking, CTF, or security certification topics", Tier.TIER_1, get_security_reference, role="practice", team="hacking"),
        "check_credential_hygiene": Tool("check_credential_hygiene", "Analyze your local Bitwarden vault for reused/breached passwords via the bw CLI (requires BW_SESSION; Jarvis never touches your master password)", Tier.TIER_1, check_credential_hygiene, role="defense", team="cybersecurity"),
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
        "lookup_cve": Tool("lookup_cve", "Search NIST NVD for CVEs matching a service/version string (public API, read-only)", Tier.TIER_1, lookup_cve, role="practice", team="hacking"),
        "search_exploitdb": Tool("search_exploitdb", "Search Exploit-DB for public exploits matching a search term (public API, read-only, no exploit execution)", Tier.TIER_1, search_exploitdb, role="practice", team="hacking"),
        "run_recon_pipeline": Tool("run_recon_pipeline", "Full recon pipeline (host discovery -> port scan -> service ID -> CVE lookup) against an authorized target only", Tier.TIER_3, run_recon_pipeline, role="offense", team="hacking"),
        "shodan_lookup": Tool("shodan_lookup", "Look up an authorized IP/domain on Shodan (passive, requires SHODAN_API_KEY, still gated by authorized_targets.json)", Tier.TIER_1, shodan_lookup, role="offense", team="hacking"),
        "run_privesc_enum": Tool("run_privesc_enum", "Run privilege escalation enumeration on the local machine (always authorized, local only)", Tier.TIER_1, run_privesc_enum, role="offense", team="hacking"),
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
        "identify_hash": Tool("identify_hash", "Identify a hash's type from its length/format (no cracking, informational only)", Tier.TIER_1, identify_hash, role="practice", team="hacking"),
        "crack_hash": Tool("crack_hash", "Attempt to crack a hash via hashcat/john for a CTF or practice lab. REQUIRES a 'source' arg of 'ctf' or 'practice' — refuses otherwise.", Tier.TIER_2, crack_hash, role="practice", team="hacking"),
        "ctf_assistant": Tool("ctf_assistant", "Analyze a CTF challenge description and return category + methodology context for the LLM", Tier.TIER_1, ctf_assistant, role="practice", team="hacking"),
        "cert_study_session": Tool("cert_study_session", "Generate a study session context for a security certification (CEH, OSCP, Security+, etc.)", Tier.TIER_1, cert_study_session, role="practice", team="hacking"),
        "generate_pentest_report": Tool("generate_pentest_report", "Generate a markdown pentest report from recon results/findings, saved to data/pentest_reports/", Tier.TIER_2, generate_pentest_report, role="practice", team="hacking"),
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
        "dns_recon": Tool("dns_recon", "Resolve A/AAAA/MX/TXT/NS/CNAME records for an authorized domain via nslookup", Tier.TIER_1, dns_recon, role="offense", team="hacking"),
        "subdomain_enum": Tool("subdomain_enum", "Passively enumerate subdomains for an authorized domain via public certificate-transparency logs (crt.sh) — nothing sent to the target itself", Tier.TIER_1, subdomain_enum, role="offense", team="hacking"),
        "whois_lookup": Tool("whois_lookup", "WHOIS registration lookup for an authorized domain/IP (requires a local whois client)", Tier.TIER_1, whois_lookup, role="offense", team="hacking"),
        "tech_fingerprint": Tool("tech_fingerprint", "Passively fingerprint the tech stack (server, CMS, JS frameworks) of an authorized URL from one GET request", Tier.TIER_1, tech_fingerprint, role="offense", team="hacking"),
        "check_ssl_cert": Tool("check_ssl_cert", "Check an authorized host's TLS certificate: issuer, validity window, days until expiry", Tier.TIER_1, check_ssl_cert, role="offense", team="hacking"),
        "http_security_headers_audit": Tool("http_security_headers_audit", "Audit an authorized URL for standard defensive HTTP headers (HSTS, CSP, X-Frame-Options, ...) — same checks as securityheaders.com", Tier.TIER_1, http_security_headers_audit, role="offense", team="hacking"),
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
        # mitre/owasp are defensive reference material — Cybersecurity team's domain.
        "mitre_attack_lookup": Tool("mitre_attack_lookup", "Look up a MITRE ATT&CK tactic or technique by name, ID, or keyword (curated reference set)", Tier.TIER_1, mitre_attack_lookup, role="practice", team="cybersecurity"),
        "owasp_top10_reference": Tool("owasp_top10_reference", "Look up an OWASP Top 10 (2021) category — example, impact, mitigation. No topic returns all ten.", Tier.TIER_1, owasp_top10_reference, role="practice", team="cybersecurity"),
        # exploitdb/reverse-shell reference is what Hacking team consults mid-engagement.
        "exploitdb_usage": Tool("exploitdb_usage", "Fetch the published usage notes/source for a specific Exploit-DB entry by ID — read-only, same public content search_exploitdb links to", Tier.TIER_1, exploitdb_usage, role="practice", team="hacking"),
        "reverse_shell_cheatsheet": Tool("reverse_shell_cheatsheet", "Static reference: common reverse-shell one-liners (bash/python/perl/php/powershell/nc/socat), filled in with your listener's LHOST/LPORT if given", Tier.TIER_1, reverse_shell_cheatsheet, role="practice", team="hacking"),
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
        "generate_payload": Tool("generate_payload", "Generate an attack payload via msfvenom for an authorized target — writes a file only, never executes/deploys it (Tier 4: requires explicit confirmation)", Tier.TIER_4, generate_payload, role="offense", team="hacking"),
        "run_exploit_module": Tool("run_exploit_module", "Run one exact, named Metasploit exploit module against an authorized target, one shot, no session chaining (Tier 4: requires explicit confirmation)", Tier.TIER_4, run_exploit_module, role="offense", team="hacking"),
        "msf_module_info": Tool("msf_module_info", "Read-only: search Metasploit modules matching a term and return the top match's documentation — no module is run", Tier.TIER_1, msf_module_info, role="practice", team="hacking"),
    }
    ALL_TOOLS = {**ALL_TOOLS, **exploit_tools_reg}
except Exception as _exploit_err:
    pass  # Exploit tools unavailable — Jarvis continues without them

# --- Dependency Vulnerability Audit (defense — your own code, not a remote target) ---
try:
    from security_modules.dependency_audit import check_dependency_vulnerabilities

    dependency_tools = {
        "check_dependency_vulnerabilities": Tool("check_dependency_vulnerabilities", "Check a local requirements.txt's exactly-pinned packages against known CVEs via OSV.dev (defaults to this project's own requirements.txt)", Tier.TIER_1, check_dependency_vulnerabilities, role="defense", team="cybersecurity"),
    }
    ALL_TOOLS = {**ALL_TOOLS, **dependency_tools}
except Exception as _dep_err:
    pass  # Dependency audit tool unavailable — Jarvis continues without it

# --- Hacking reference library -> tool synthesis (Phase 6 item 3) ---
# Confirmed with Devin: synthesis may generate genuinely new script content (not just
# recombine existing tools), tightly gated — see hacking_synthesis.py's module docstring
# for the three safety properties enforced in code. synthesize_tool_from_reference only
# ever writes a file + proposes a skill (Tier 4, same as generate_payload); the ONE tool
# that can actually run a synthesized script (run_synthesized_script) is also Tier 4 and
# re-checks authorization on every call regardless of prior approvals — role="offense" so
# the same centralized _check_offense_authorization gate every other offense tool goes
# through in coordinator.py also applies here, on top of the tool's own internal check.
try:
    from hacking_synthesis import (
        add_reference_material, list_reference_material, read_reference_material,
        synthesize_tool_from_reference, run_synthesized_script,
    )

    hacking_synthesis_reg = {
        "add_reference_material": Tool("add_reference_material", "Add a note/script/writeup to your hacking reference library for the Hacking team to draw from", Tier.TIER_2, add_reference_material, team="hacking"),
        "list_reference_material": Tool("list_reference_material", "List everything currently in your hacking reference library", Tier.TIER_1, list_reference_material, team="hacking"),
        "read_reference_material": Tool("read_reference_material", "Read a specific file from your hacking reference library", Tier.TIER_1, read_reference_material, team="hacking"),
        "synthesize_tool_from_reference": Tool("synthesize_tool_from_reference", "Generate a NEW script from reference material for a stated goal — only ever writes the file and proposes it as a skill for review (Tier 4: requires explicit confirmation), never executes anything", Tier.TIER_4, synthesize_tool_from_reference, role="offense", team="hacking"),
        "run_synthesized_script": Tool("run_synthesized_script", "Run a previously-synthesized script against an authorized target — re-checks authorized_targets.json on every call regardless of prior approvals (Tier 4: requires explicit confirmation)", Tier.TIER_4, run_synthesized_script, role="offense", team="hacking"),
    }
    ALL_TOOLS = {**ALL_TOOLS, **hacking_synthesis_reg}
except Exception as _synth_err:
    pass  # Hacking synthesis tools unavailable — Jarvis continues without them

# --- Twingate fail-closed gate (Phase 8, section 5) — the status check + override toggle
# every firewall/NAS tool below is gated behind. get_twingate_status is Tier 1 (read-only
# visibility); the two toggle tools are Tier 3 per Devin's own spec ("explicit, logged,
# Tier-3 toggle... never a silent fallback") — notify-then-act, not instant, for both
# directions, so re-enabling protection gets the same visible moment disabling it does.
try:
    from twingate_status import (
        twingate_status, disable_twingate_requirement, enable_twingate_requirement,
    )

    twingate_reg = {
        "get_twingate_status": Tool("get_twingate_status", "Check whether the Twingate client is installed and connected right now — the gate every firewall/NAS action is refused behind if this is False", Tier.TIER_1, twingate_status, team="it"),
        "disable_twingate_requirement": Tool("disable_twingate_requirement", "Temporarily allow firewall/NAS actions to proceed without a live Twingate connection (e.g. known flakiness while traveling) — requires a reason, auto-expires (default 60min, max 24h), logged every time", Tier.TIER_3, disable_twingate_requirement, team="it"),
        "enable_twingate_requirement": Tool("enable_twingate_requirement", "Re-enable the Twingate connectivity requirement immediately, before its natural expiry", Tier.TIER_3, enable_twingate_requirement, team="it"),
    }
    ALL_TOOLS = {**ALL_TOOLS, **twingate_reg}
except Exception as _tg_err:
    pass  # Twingate status tools unavailable — Jarvis continues without them

# --- OPNsense firewall (Phase 8, section 1) — every read tool is gated behind
# require_twingate_or_refuse() inside firewall_tools.py itself, not here; every
# rule-mutating tool is Tier 4 with role="offense" per Devin's own "every time, no
# exceptions" instruction (a misconfiguration here can lock him out of his own network).
try:
    from firewall_tools import (
        save_opnsense_credentials, get_connected_devices, get_bandwidth_usage,
        get_active_connections, get_firewall_uptime, get_firewall_logs,
        get_firewall_traffic_patterns, add_firewall_rule, remove_firewall_rule, toggle_port,
    )

    firewall_reg = {
        "save_opnsense_credentials": Tool("save_opnsense_credentials", "Save OPNsense API credentials (base_url, api_key, api_secret from OPNsense's System -> Access -> API Keys) — encrypted at rest, same as the Google tokens", Tier.TIER_2, save_opnsense_credentials, team="network"),
        "get_connected_devices": Tool("get_connected_devices", "Read the firewall's live ARP table — every device currently seen on the network", Tier.TIER_1, get_connected_devices, team="network"),
        "get_bandwidth_usage": Tool("get_bandwidth_usage", "Read per-interface bandwidth counters from the firewall", Tier.TIER_1, get_bandwidth_usage, team="network"),
        "get_active_connections": Tool("get_active_connections", "Read the firewall's current active-connection state table", Tier.TIER_1, get_active_connections, team="network"),
        "get_firewall_uptime": Tool("get_firewall_uptime", "Read the firewall's own reported uptime/system status", Tier.TIER_1, get_firewall_uptime, team="network"),
        "get_firewall_logs": Tool("get_firewall_logs", "Read recent firewall filter logs for anomaly detection", Tier.TIER_1, get_firewall_logs, team="cybersecurity"),
        "get_firewall_traffic_patterns": Tool("get_firewall_traffic_patterns", "Read a combined traffic-pattern snapshot (interface stats + active connections) for anomaly analysis", Tier.TIER_1, get_firewall_traffic_patterns, team="cybersecurity"),
        "add_firewall_rule": Tool("add_firewall_rule", "Add and apply ONE firewall rule — requires explicit confirmation every single call, no exceptions (a misconfiguration here can lock Devin out of his own network)", Tier.TIER_4, add_firewall_rule, role="offense", team="network"),
        "remove_firewall_rule": Tool("remove_firewall_rule", "Remove and apply removal of one firewall rule by its uuid — requires explicit confirmation every single call", Tier.TIER_4, remove_firewall_rule, role="offense", team="network"),
        "toggle_port": Tool("toggle_port", "Enable or disable one existing firewall rule by its uuid (opens/closes a port without deleting the rule) — requires explicit confirmation every single call", Tier.TIER_4, toggle_port, role="offense", team="network"),
    }
    ALL_TOOLS = {**ALL_TOOLS, **firewall_reg}
except Exception as _fw_err:
    pass  # OPNsense firewall tools unavailable — Jarvis continues without them

# --- UGREEN NAS (Phase 8, section 2) — same Twingate-gated, credential-configured pattern
# as the firewall tools above, owned by the IT team (matches IT's existing ownership of
# backups/local file operations/scheduled maintenance).
try:
    from nas_tools import (
        save_nas_credentials, get_nas_storage_health, get_nas_capacity,
        get_nas_backup_status, get_nas_running_services, create_nas_backup, read_nas_file,
        delete_nas_file, update_nas_config, modify_nas_share_permissions,
    )

    nas_reg = {
        "save_nas_credentials": Tool("save_nas_credentials", "Save NAS login credentials (base_url, username, password) — encrypted at rest, same as the Google tokens", Tier.TIER_2, save_nas_credentials, team="it"),
        "get_nas_storage_health": Tool("get_nas_storage_health", "Read disk-level health from the NAS's storage subsystem", Tier.TIER_1, get_nas_storage_health, team="it"),
        "get_nas_capacity": Tool("get_nas_capacity", "Read storage pool capacity/usage from the NAS", Tier.TIER_1, get_nas_capacity, team="it"),
        "get_nas_backup_status": Tool("get_nas_backup_status", "Read recent/scheduled backup task status from the NAS", Tier.TIER_1, get_nas_backup_status, team="it"),
        "get_nas_running_services": Tool("get_nas_running_services", "Read what's currently running on the NAS", Tier.TIER_1, get_nas_running_services, team="it"),
        "create_nas_backup": Tool("create_nas_backup", "Trigger a one-off backup job on the NAS for a given path — reversible/logged, doesn't overwrite or remove anything", Tier.TIER_2, create_nas_backup, team="it"),
        "read_nas_file": Tool("read_nas_file", "Read a file's actual content from the NAS (not just metadata)", Tier.TIER_2, read_nas_file, team="it"),
        "delete_nas_file": Tool("delete_nas_file", "Delete one file/path on the NAS — requires explicit confirmation every single call", Tier.TIER_4, delete_nas_file, role="offense", team="it"),
        "update_nas_config": Tool("update_nas_config", "Change one NAS-level config setting — requires explicit confirmation every single call", Tier.TIER_4, update_nas_config, role="offense", team="it"),
        "modify_nas_share_permissions": Tool("modify_nas_share_permissions", "Change one user's permission level on one shared folder — requires explicit confirmation every single call", Tier.TIER_4, modify_nas_share_permissions, role="offense", team="it"),
    }
    ALL_TOOLS = {**ALL_TOOLS, **nas_reg}
except Exception as _nas_err:
    pass  # NAS tools unavailable — Jarvis continues without them

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
    "run_self_audit": "filesystem",  # shells to git ls-files, reads devices.json
    # Obsidian vault tools (Phase 6) — reach the server host's local vault directory.
    "list_vault_structure": "filesystem",
    "read_note": "filesystem",
    "search_vault": "filesystem",
    "create_note": "filesystem",
    "append_note": "filesystem",
    "overwrite_note": "filesystem",
    "delete_note": "filesystem",
    "index_vault_into_memory": "filesystem",
    # Hacking reference library / tool synthesis (Phase 6 item 3) — reads/writes the
    # server host's local reference & synthesized-tool directories, and (for
    # run_synthesized_script) shells out on the server host.
    "add_reference_material": "filesystem",
    "list_reference_material": "filesystem",
    "read_reference_material": "filesystem",
    "synthesize_tool_from_reference": "filesystem",
    "run_synthesized_script": "filesystem",
    # Network team tools (Phase 4) — all reach the local network the server sits on.
    "ping_sweep": "filesystem",
    "check_latency": "filesystem",          # pings from wherever the server host actually is
    "arp_table_snapshot": "filesystem",     # reads the server host's own ARP table
    "bandwidth_sample": "filesystem",       # psutil reads the server host's own interfaces
    "check_wan_status": "filesystem",       # SSDP multicast from the server host
    "diagnose_connectivity": "filesystem",  # composes the above
    # Uptime Kuma — HTTP calls to localhost:3001 on the server host specifically.
    "get_uptime_kuma_status": "filesystem",
    # Twingate gate (Phase 8) — shells out to the twingate CLI on the server host.
    "get_twingate_status": "filesystem",
    "disable_twingate_requirement": "filesystem",
    "enable_twingate_requirement": "filesystem",
    # OPNsense firewall (Phase 8) — reads/writes the server host's own encrypted
    # credential file and makes outbound API calls from wherever the server host is.
    "save_opnsense_credentials": "filesystem",
    "get_connected_devices": "filesystem",
    "get_bandwidth_usage": "filesystem",
    "get_active_connections": "filesystem",
    "get_firewall_uptime": "filesystem",
    "get_firewall_logs": "filesystem",
    "get_firewall_traffic_patterns": "filesystem",
    "add_firewall_rule": "filesystem",
    "remove_firewall_rule": "filesystem",
    "toggle_port": "filesystem",
    # UGREEN NAS (Phase 8) — same reasoning as the OPNsense entries above.
    "save_nas_credentials": "filesystem",
    "get_nas_storage_health": "filesystem",
    "get_nas_capacity": "filesystem",
    "get_nas_backup_status": "filesystem",
    "get_nas_running_services": "filesystem",
    "create_nas_backup": "filesystem",
    "read_nas_file": "filesystem",
    "delete_nas_file": "filesystem",
    "update_nas_config": "filesystem",
    "modify_nas_share_permissions": "filesystem",
}

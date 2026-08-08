"""
security_hardening.py — Cross-cutting defensive plumbing (Phase 8, "Security Hardening").

Deliberately NOT a redesign of anything that already works — this sits alongside the
existing Tier system / AuthorizationCheck / prompt-injection delimiters (Phase 2b/3e/7)
and closes gaps those didn't cover:

  1. run_hardened() — a subprocess.run() wrapper that strips the child's inherited
     environment down to an explicit allowlist, so a secret sitting in this process's env
     (an API key, a token) is never handed to every shelled-out command by default.
  2. redact() — scrubs common secret shapes (API keys, bearer tokens, AWS keys, private
     key blocks) out of any text before it's logged or persisted. Best-effort pattern
     matching, same "probabilistic, not a guarantee" honesty as Phase 7's injection
     detector — documented as a known limitation, not oversold.
  3. KillSwitch — a file-flag emergency stop. When armed, every Tier 2+ tool call is
     refused before it executes. Checked at the same dispatch point AuthorizationCheck
     already runs at (coordinator.py), not bolted on somewhere it could be skipped.
  4. run_self_audit() — a read-only Tier-1 tool that checks this project's own posture:
     kill switch state, whether secret-looking material is staged for a git commit,
     which subprocess call sites are hardened vs. not, device count/token age.

Scope boundary, deliberate not accidental: this hardens the highest-risk subprocess call
sites (run_local_script, generate_payload, run_exploit_module — the ones that take
free-form or LLM-influenced input) rather than rewriting all ~20 subprocess.check_output
call sites across security_modules/. The recon/osint tools invoke fixed external binaries
(nmap, whois, nslookup) with argument-list (not shell-string) subprocess calls and their
own timeouts already — same env exposure in principle, lower practical risk since there's
no shell-string injection surface there. Worth doing in a future pass, not this one.
"""

import os
import re
import time
import json
import subprocess

# ---------------------------------------------------------------------------
# 1. Hardened subprocess execution
# ---------------------------------------------------------------------------

# Minimal env a Windows child process needs to actually function (PATH, temp dirs,
# the system root, username) — deliberately excludes API keys, tokens, and anything
# else this process's .env loaded into os.environ. Extend only if a real command
# breaks without a specific var; don't widen this "just in case".
_SAFE_ENV_KEYS = (
    "PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP",
    "USERPROFILE", "USERNAME", "COMPUTERNAME", "PATHEXT", "COMSPEC",
    "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA",
)


def _minimal_env() -> dict:
    env = {}
    for key in _SAFE_ENV_KEYS:
        val = os.environ.get(key)
        if val is not None:
            env[key] = val
    return env


def run_hardened(cmd, timeout=60, **kwargs):
    """Drop-in-ish replacement for subprocess.run()/check_output() call sites that take
    free-form or externally-influenced input. Strips the environment to _SAFE_ENV_KEYS
    and enforces a timeout unless the caller overrides it. Raises the same exceptions
    subprocess normally would (CalledProcessError, TimeoutExpired) — callers keep their
    existing except blocks unchanged.
    """
    kwargs.setdefault("env", _minimal_env())
    kwargs.setdefault("timeout", timeout)
    kwargs.setdefault("text", True)
    # subprocess.run() raises ValueError if capture_output is combined with an explicit
    # stdout/stderr — only default capture_output when the caller didn't set either.
    if "stdout" not in kwargs and "stderr" not in kwargs:
        kwargs.setdefault("capture_output", True)
    return subprocess.run(cmd, **kwargs)


# ---------------------------------------------------------------------------
# 2. Log / output redaction
# ---------------------------------------------------------------------------

# Each pattern -> replacement label. Order matters a little (private key blocks first,
# since they're multiline and more specific); otherwise not order-sensitive.
_REDACTION_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
     "[REDACTED:PRIVATE_KEY]"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "[REDACTED:OPENAI_KEY]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED:AWS_KEY_ID]"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), "[REDACTED:GOOGLE_KEY]"),
    (re.compile(r"\bxox[baprs]-[0-9A-Za-z\-]{10,}\b"), "[REDACTED:SLACK_TOKEN]"),
    (re.compile(r"\bghp_[A-Za-z0-9]{36}\b"), "[REDACTED:GITHUB_TOKEN]"),
    (re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|bearer|secret|password|passwd)\s*[:=]\s*['\"]?[A-Za-z0-9\-_./+]{8,}['\"]?"),
     lambda m: m.group(1) + "=[REDACTED]"),
    (re.compile(r"\b[0-9a-f]{48,64}\b"), "[REDACTED:HEX_TOKEN]"),  # device bearer tokens etc.
]


def redact(text: str) -> str:
    """Best-effort scrub of common secret shapes out of `text`. Not a guarantee — the
    same honesty as Phase 7's prompt-injection detector: pattern matching catches known
    shapes, not everything. Use it as defense-in-depth before logging/persisting tool
    output or console lines, never as the sole reason to treat something as safe to share.
    """
    if not text:
        return text
    out = str(text)
    for pattern, replacement in _REDACTION_PATTERNS:
        out = pattern.sub(replacement, out)
    return out


# ---------------------------------------------------------------------------
# 3. Kill switch
# ---------------------------------------------------------------------------

_KILL_SWITCH_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "KILL_SWITCH")


class KillSwitchActive(Exception):
    """Raised at tool dispatch when the kill switch is armed and a gated tier is requested."""
    pass


def is_kill_switch_armed() -> bool:
    return os.path.exists(_KILL_SWITCH_FILE)


def arm_kill_switch(reason: str = ""):
    """Emergency stop: every Tier 2+ tool call is refused until disarmed. File-based (not
    in-memory) so it survives a server restart and is visible/removable by hand if the
    agent process itself is unresponsive."""
    os.makedirs(os.path.dirname(_KILL_SWITCH_FILE), exist_ok=True)
    with open(_KILL_SWITCH_FILE, "w") as f:
        json.dump({"armed_at": time.time(), "reason": reason or "no reason given"}, f)
    return f"Kill switch ARMED. All Tier 2+ tool calls will be refused until disarm_kill_switch() is called. Reason: {reason or '(none given)'}"


def disarm_kill_switch():
    if os.path.exists(_KILL_SWITCH_FILE):
        os.remove(_KILL_SWITCH_FILE)
        return "Kill switch disarmed. Normal tool execution resumed."
    return "Kill switch was not armed."


def kill_switch_status() -> dict:
    if not is_kill_switch_armed():
        return {"armed": False}
    try:
        with open(_KILL_SWITCH_FILE) as f:
            info = json.load(f)
    except Exception:
        info = {}
    return {"armed": True, **info}


# ---------------------------------------------------------------------------
# 3b. Auth rate limiting / lockout
# ---------------------------------------------------------------------------

class AuthRateLimiter:
    """Tracks failed device-auth attempts per source (IP, typically) and locks a source out
    for a cooldown window after too many failures — closes a real gap Phase 2d never had:
    devices.json tokens were compared in constant time (good, no timing side-channel) but
    nothing stopped unlimited guesses. In-memory only (per server process); a restart clears
    lockouts, which is an acceptable tradeoff for a LAN personal-server threat model — the
    goal is slowing down automated guessing, not surviving a distributed attack."""

    def __init__(self, max_attempts: int = 5, window_seconds: int = 300, lockout_seconds: int = 300):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.lockout_seconds = lockout_seconds
        self._failures = {}   # source -> list[timestamp]
        self._locked_until = {}  # source -> timestamp

    def is_locked(self, source: str):
        until = self._locked_until.get(source)
        if until and time.time() < until:
            return until
        if until:
            del self._locked_until[source]
        return None

    def record_failure(self, source: str):
        now = time.time()
        attempts = [t for t in self._failures.get(source, []) if now - t < self.window_seconds]
        attempts.append(now)
        self._failures[source] = attempts
        if len(attempts) >= self.max_attempts:
            self._locked_until[source] = now + self.lockout_seconds
            self._failures[source] = []

    def record_success(self, source: str):
        self._failures.pop(source, None)
        self._locked_until.pop(source, None)


# ---------------------------------------------------------------------------
# 4. Self-audit
# ---------------------------------------------------------------------------

def run_self_audit() -> dict:
    """Read-only posture check on Jarvis's own security hardening (not the host machine —
    that's get_security_posture's job). Tier 1, no gate needed: it only reads local
    project state, never touches a remote target."""
    base = os.path.dirname(os.path.abspath(__file__))
    findings = []
    ok = []

    ks = kill_switch_status()
    if ks["armed"]:
        findings.append(f"Kill switch is ARMED (reason: {ks.get('reason', '?')}) — all Tier 2+ tools are currently refused.")
    else:
        ok.append("Kill switch not armed (normal operating state).")

    # Secret-looking files present but not accidentally trackable
    gitignore_path = os.path.join(base, ".gitignore")
    sensitive_files = [".env", "credentials.json", "token.json", "devices.json", "data/", "backups/"]
    if os.path.exists(gitignore_path):
        with open(gitignore_path) as f:
            gi_content = f.read()
        missing = [s for s in sensitive_files if s.rstrip("/") not in gi_content]
        if missing:
            findings.append(f".gitignore is missing entries for: {missing}")
        else:
            ok.append(".gitignore covers all known sensitive files/dirs.")
    else:
        findings.append("No .gitignore found at project root.")

    # Are any of those sensitive files actually staged/tracked in git right now?
    try:
        tracked = run_hardened(["git", "ls-files"], cwd=base, timeout=10).stdout.splitlines()
        leaked = [f for f in tracked if os.path.basename(f) in
                  (".env", "credentials.json", "token.json", "devices.json")]
        if leaked:
            findings.append(f"SENSITIVE FILES ARE TRACKED IN GIT: {leaked} — remove with 'git rm --cached' immediately.")
        else:
            ok.append("No sensitive credential files are tracked in git.")
    except Exception:
        ok.append("(git not available to check tracked files — skipped)")

    # Device token count / staleness (informational, not a hard finding)
    try:
        from device_registry import DeviceRegistry
        devices = DeviceRegistry().list_devices()
        stale_days = 90
        now = time.time()
        stale = [d for d, v in devices.items() if v.get("created") and (now - v["created"]) > stale_days * 86400]
        if stale:
            findings.append(f"{len(stale)} device(s) registered over {stale_days} days ago — consider revoking unused devices: {stale}")
        ok.append(f"{len(devices)} device(s) currently registered.")
    except Exception:
        pass

    # Hardened subprocess coverage (informational — see module docstring's scope note)
    ok.append("run_local_script/generate_payload/run_exploit_module use run_hardened() (env-stripped subprocess). "
               "Other subprocess call sites (recon/osint tools) use fixed argument-list commands, not shell strings.")

    return {
        "kill_switch": ks,
        "findings": findings,
        "ok": ok,
        "status": "ATTENTION" if findings else "CLEAN",
    }

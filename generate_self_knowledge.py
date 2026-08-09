"""
generate_self_knowledge.py — Phase 8b, "give Jarvis real self-knowledge".

Generates context/self/jarvis.md: a snapshot document describing what Jarvis actually is
right now — tool inventory, tier/role breakdown, security posture, recent capability
history — built by introspecting the live ALL_TOOLS registry and other real state rather
than hand-maintained prose that quietly goes stale.

Design decision, adapted from the source spec rather than followed literally: that spec
assumed the self-knowledge doc gets stuffed into the system prompt every turn. This
codebase already does the equivalent for the one thing that actually needs to be accurate
turn-to-turn — llm.py's get_tool_descriptions() dynamically lists every real tool from
ALL_TOOLS in every system prompt already (see llm.py). Re-injecting the full doc on top of
that would just double token cost for duplicate information. Instead: this doc is exposed
on demand via a new `get_self_knowledge` tool (Tier 1) — the LLM calls it when a question
is actually about Jarvis's own architecture/limits/history, not on every single turn.

Usage:
    python generate_self_knowledge.py          # regenerate context/self/jarvis.md
    python generate_self_knowledge.py --check  # exit 1 if the file would change (no write) —
                                                # used by the pre-commit drift-check hook
"""

import os
import re
import sys
import time
from datetime import datetime, timezone

_BASE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATH = os.path.join(_BASE, "context", "self", "jarvis.md")
TASK_MD_PATH = os.path.join(_BASE, "task.md")


def _tool_inventory_section() -> str:
    from tools import ALL_TOOLS, REQUIRES_CAPABILITY

    by_role = {}
    for name, tool in sorted(ALL_TOOLS.items()):
        role = tool.role or "general"
        by_role.setdefault(role, []).append(tool)

    lines = []
    role_order = ["general", "defense", "offense", "practice"]
    role_label = {
        "general": "General (assistant/ops)",
        "defense": "Defense",
        "offense": "Offense (authorization-gated)",
        "practice": "Practice / Reference",
    }
    for role in role_order:
        tools = by_role.get(role, [])
        if not tools:
            continue
        lines.append(f"### {role_label[role]} ({len(tools)})\n")
        lines.append("| Tool | Tier | Local-machine? | Description |")
        lines.append("|---|---|---|---|")
        for tool in tools:
            local = "yes" if tool.name in REQUIRES_CAPABILITY else ""
            desc = tool.description.replace("|", "/")
            lines.append(f"| `{tool.name}` | {tool.tier.name} | {local} | {desc} |")
        lines.append("")
    return "\n".join(lines)


def _tier_role_stats() -> str:
    from tools import ALL_TOOLS, Tier

    total = len(ALL_TOOLS)
    tier_counts = {t: 0 for t in Tier}
    role_counts = {}
    for tool in ALL_TOOLS.values():
        tier_counts[tool.tier] += 1
        role = tool.role or "general"
        role_counts[role] = role_counts.get(role, 0) + 1

    lines = [f"**{total} tools registered total.**\n"]
    lines.append("By tier (what kind of oversight each requires before running):")
    tier_meaning = {
        Tier.TIER_1: "read-only, no approval needed",
        Tier.TIER_2: "reversible/logged, no approval needed",
        Tier.TIER_3: "notify-then-act, 3s window to cancel",
        Tier.TIER_4: "explicit confirmation required every call",
    }
    for tier in Tier:
        lines.append(f"- Tier {tier.value} ({tier_meaning[tier]}): {tier_counts[tier]}")
    lines.append("\nBy role (security specialist taxonomy — general/ops tools have no role tag):")
    for role, count in sorted(role_counts.items()):
        lines.append(f"- {role}: {count}")
    return "\n".join(lines)


def _security_posture_section() -> str:
    try:
        from security_hardening import run_self_audit
        audit = run_self_audit()
    except Exception as e:
        return f"(self-audit unavailable: {e})"

    lines = [f"**Status: {audit['status']}**\n"]
    for item in audit["ok"]:
        lines.append(f"- OK: {item}")
    for item in audit["findings"]:
        lines.append(f"- ATTENTION: {item}")
    return "\n".join(lines)


def _test_coverage_section() -> str:
    import glob
    test_files = sorted(glob.glob(os.path.join(_BASE, "test_*.py")))
    return f"{len(test_files)} test files in the project root (run any individually with `python <file>`; each prints its own pass/fail count — no shared runner/CI config exists yet)."


def _recent_phase_history(max_phases: int = 5) -> str:
    """Pulls the most recent '- [x] **PhaseN**: ...' top-level bullet lines out of task.md —
    the project's own running changelog — rather than duplicating that history by hand here.
    Best-effort regex parse; if task.md's format ever changes this just yields fewer/no
    entries, it doesn't raise."""
    if not os.path.exists(TASK_MD_PATH):
        return "(task.md not found)"
    with open(TASK_MD_PATH, encoding="utf-8") as f:
        content = f.read()

    # Top-level phase bullets only (not the indented sub-bullets under them).
    matches = re.findall(r'^- \[x\] \*\*(Phase [^\*]+)\*\*: (.+)$', content, re.MULTILINE)
    if not matches:
        return "(no phase entries parsed from task.md)"

    recent = matches[-max_phases:]
    lines = []
    for phase, summary in recent:
        summary = summary.strip()
        if len(summary) > 240:
            summary = summary[:240].rsplit(" ", 1)[0] + "…"
        lines.append(f"- **{phase}**: {summary}")
    return "\n".join(lines)


def generate() -> str:
    """Build the full document as a string (doesn't touch disk)."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    doc = f"""# Jarvis — Self-Knowledge

_Auto-generated by `generate_self_knowledge.py` — do not hand-edit, changes will be
overwritten. Regenerate with `python generate_self_knowledge.py`. Last generated: {now}._

## What I am

I'm Jarvis, Devin's personal AI agent — not a general-purpose chatbot. I run as a
single long-lived server process (`server.py`) on Devin's own machine, reachable from
multiple devices (desktop GUI, CLI, voice companion) over a shared WebSocket protocol, with
conversation history, projects, and file uploads persisted to a local SQLite database
(`data/jarvis.db`). Nothing about me runs in the cloud except the LLM API calls themselves
(and even those are optional — I run fully offline against a local Ollama model with zero
API keys configured).

I operate under a tiered approval system for everything I do (Tier 1 read-only through
Tier 4 explicit-confirmation-every-time), and a security-specialist mode with its own
offense/defense/practice taxonomy and a hard authorization allowlist for anything that
touches a network target that isn't Devin's own machine.

## Tool inventory

{_tool_inventory_section()}

## Tier & role breakdown

{_tier_role_stats()}

## Current security posture

(Live snapshot at generation time — call `run_self_audit()` directly for a fresh read.)

{_security_posture_section()}

## Test coverage

{_test_coverage_section()}

## Recent capability history

(Most recent phases from task.md — see that file for the complete history.)

{_recent_phase_history()}

## Known, deliberate scope boundaries

These are documented choices, not gaps someone forgot:
- Offensive security tools only ever act against targets in `data/authorized_targets.json` —
  there is no path to scan/exploit/generate a payload for anything not explicitly
  pre-authorized.
- `generate_payload` only ever writes a file; it never executes, transfers, or deploys it.
- `run_exploit_module` runs exactly one named module, one shot, no session chaining — a
  human decides what to do with any session it reports, not Jarvis.
- `/upload`, `/transcribe`, and `/export/{{id}}` are unauthenticated HTTP endpoints by
  current design (only the WebSocket handshake enforces device tokens) — a real boundary
  worth closing if this server is ever exposed beyond a trusted LAN.
"""
    return doc


def main():
    check_only = "--check" in sys.argv
    new_content = generate()

    if check_only:
        old_content = None
        if os.path.exists(OUTPUT_PATH):
            with open(OUTPUT_PATH, encoding="utf-8") as f:
                old_content = f.read()
        # Ignore the "Last generated" timestamp line when diffing for drift — that always
        # differs run-to-run and isn't the kind of drift this check exists to catch.
        strip_ts = lambda s: re.sub(r"Last generated: .*?\._", "", s or "")
        if strip_ts(old_content) != strip_ts(new_content):
            print("context/self/jarvis.md is stale — run `python generate_self_knowledge.py` and commit it.")
            sys.exit(1)
        print("context/self/jarvis.md is up to date.")
        sys.exit(0)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(new_content)
    print(f"Wrote {OUTPUT_PATH} ({len(new_content)} chars).")


def get_self_knowledge() -> str:
    """Tool entrypoint (Tier 1): returns the self-knowledge doc's live content. Regenerates
    on the fly rather than reading a possibly-stale file from disk — this is what makes it
    safe to call even if the pre-commit hook hasn't run recently; the committed file on disk
    is for humans/git history, this function is what the LLM actually sees."""
    return generate()


if __name__ == "__main__":
    main()

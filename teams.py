"""
teams.py — Phase 4: the 5 subagent teams, each a scoped view over the same ALL_TOOLS
registry rather than a separate engine. See team_router.py's module docstring for why
("shared engine, scoped prompts" — confirmed with Devin before building this).

A Team is just: a name, the set of tool names it's allowed to call (filtered from
ALL_TOOLS by `team=`), and a short prompt fragment describing its scope. The actual
reasoning loop is JarvisBrain/Coordinator, unchanged — team_router.py invokes it once per
routed team with that team's tool subset substituted in.

Tools with team=None (get_self_knowledge) are coordinator-level and appended to every
team's tool set — see tools.py's registration comments for why it specifically stays
outside any one team's ownership.
"""

from tools import ALL_TOOLS


class Team:
    def __init__(self, key: str, display_name: str, aliases: list, scope_prompt: str, user_summary: str):
        self.key = key
        self.display_name = display_name
        self.aliases = aliases  # lowercase phrases that address this team explicitly
        self.scope_prompt = scope_prompt
        # A plain-English, one-sentence description for the GUI's Teams panel — deliberately
        # NOT the same text as scope_prompt. scope_prompt is written as an instruction *to*
        # the model ("You are the Network team. You own..."); showing that verbatim to a
        # human reads as the app talking to itself. user_summary is written *for* a person
        # reading the panel instead.
        self.user_summary = user_summary

    def tool_names(self) -> set:
        """Live-computed, not cached at import time — a tool registered/deregistered at
        runtime (e.g. an optional dependency becoming available) is picked up immediately,
        same convention ALL_TOOLS itself already follows (built once at import via try/
        except per optional module, but nothing here freezes a stale copy)."""
        owned = {name for name, tool in ALL_TOOLS.items() if tool.team == self.key}
        coordinator_level = {name for name, tool in ALL_TOOLS.items() if tool.team is None}
        return owned | coordinator_level

    def skills_prompt_fragment(self) -> str:
        """Phase 5: active, non-flagged skills for this team, injected into team_context
        (see team_router.py) as documented recipes the model can choose to follow. This is
        a prompt hint, not a new execution path — following a skill still means emitting
        normal [TOOL: ...] calls that go through the exact same tier/authorization gates
        as if the model had thought of the sequence fresh. Returns "" (no section at all)
        when there are no active skills yet, rather than an empty header."""
        import skills
        active = skills.active_skills_for_team(self.key)
        if not active:
            return ""
        lines = ["LEARNED SKILLS (optional — proven multi-step patterns for this team; "
                 "follow one if it genuinely fits, don't force it):"]
        lines.extend(s.as_prompt_blurb() for s in active)
        lines.append("If you follow one of these, end your response with exactly: [SKILL_USED: <name>]")
        return "\n".join(lines)


TEAMS = {
    "personal_assistant": Team(
        "personal_assistant", "Personal Assistant",
        aliases=["personal assistant", "assistant team", "pa team"],
        scope_prompt=(
            "You are the Personal Assistant team. You own email, tasks/reminders, drafting "
            "notes, general web search, and recalling past conversations. You do not have "
            "system administration, network, or security tools — if a request needs those, "
            "say so plainly rather than attempting a workaround."
        ),
        user_summary=(
            "Handles email, tasks and reminders, notes, and web search — and remembers "
            "what you've talked about before."
        ),
    ),
    "network": Team(
        "network", "Network",
        aliases=["network team", "networking team"],
        scope_prompt=(
            "You are the Network team. You own local device discovery (ping sweep, ARP "
            "inventory), latency checks, bandwidth sampling, and router WAN status where "
            "reachable. You diagnose connectivity — you never act on findings yourself. If "
            "you observe anomalous traffic or an unrecognized device, describe it plainly "
            "in your answer; a separate step (not you) escalates that to the Cybersecurity "
            "team when it needs one. You have no tools that change live network config."
        ),
        user_summary=(
            "Finds devices on your network, checks connection speed and latency, and flags "
            "anything unusual — it only looks, it never changes your network settings."
        ),
    ),
    "it": Team(
        "it", "IT",
        aliases=["it team", "systems team", "sysadmin team"],
        scope_prompt=(
            "You are the IT team. You own system health, event logs, backups, local file "
            "operations, usage/cost tracking, and scheduled maintenance. If you notice a "
            "patch gap or configuration drift, describe it plainly — escalating it to the "
            "Cybersecurity team is a separate step, not something you do yourself."
        ),
        user_summary=(
            "Keeps an eye on this computer's health, manages backups and files, and handles "
            "routine maintenance."
        ),
    ),
    "cybersecurity": Team(
        "cybersecurity", "Cybersecurity",
        aliases=["cybersecurity team", "security team", "defense team", "cyber team"],
        scope_prompt=(
            "You are the Cybersecurity team (defense). You own posture monitoring, breach/"
            "credential checks, dependency vulnerability audits, and MITRE ATT&CK/OWASP "
            "reference. You are also the authority other teams' findings get escalated to. "
            "You do not run offensive tools yourself — if verification requires active "
            "testing, that's the Hacking team's job, and it only proceeds with Devin's "
            "explicit approval, never on your recommendation alone."
        ),
        user_summary=(
            "Watches for security problems — breached passwords, vulnerable software, "
            "unusual activity — and decides when something needs a closer look."
        ),
    ),
    "hacking": Team(
        "hacking", "Hacking",
        aliases=["hacking team", "offense team", "pentest team", "red team"],
        scope_prompt=(
            "You are the Hacking team (authorized offense only). Every tool you have that "
            "touches a target checks it against data/authorized_targets.json before acting "
            "— if a target isn't listed, refuse and tell Devin exactly what to add and how, "
            "don't attempt it and don't pick a substitute target. You report findings, you "
            "do not decide remediation — that belongs to the Cybersecurity team."
        ),
        user_summary=(
            "Runs real security tests (like port scans) — but only against targets you've "
            "explicitly authorized, and only when Cybersecurity calls for it."
        ),
    ),
}


def resolve_team_alias(text: str):
    """Stage 1 of routing (team_router.py): cheap, deterministic explicit-address
    detection, no LLM call. Matches phrases like 'ask the network team...' — returns the
    matched Team or None if nothing in `text` names a team explicitly."""
    lowered = text.lower()
    for team in TEAMS.values():
        for alias in team.aliases:
            if alias in lowered:
                return team
    return None

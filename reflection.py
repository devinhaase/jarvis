"""
reflection.py — Phase 5: the autonomous half of the skill system. Runs after a
"non-trivial" turn (multi-step, or a capability denial had to be navigated), asks the
model to evaluate what happened, and — only if a genuinely reusable pattern is present —
drafts a candidate skill via skills.propose_skill(). That function always writes
status="proposed"; this module never imports or calls approve_skill, disable_skill, or
anything else that could flip a skill live. The one-way door (proposed -> active) is
entirely in skills.py, entirely human-gated — see that module's docstring.

Design note on safety: this module could, in principle, be tricked (by a cleverly-worded
tool result, or the model's own drift) into proposing a bad or malicious-looking skill.
That's an acceptable risk specifically *because* propose_skill() cannot activate anything —
a bad proposal just sits in the review queue for Devin to reject, the same blast radius as
any other unreviewed suggestion. The actual safety boundary is the review gate in skills.py,
not this module's judgment.
"""

import re

_REFLECTION_PROMPT = """You just completed a task for Devin. Reflect on it honestly and briefly.

Devin's request: "{user_message}"
Tools used, in order: {tools_summary}
Your final response to Devin: "{final_response}"

Respond in this exact tagged format and nothing else:
[REFLECTION: succeeded="<true or false>" worked="<one short phrase>" differently="<one short phrase, or 'nothing' if it went well>"]

Only if this task involved a genuinely REUSABLE multi-step pattern worth remembering for
next time — not a one-off or single-tool task — also add exactly one more line:
[SKILL_PROPOSAL: name="<short skill name>" when="<one sentence: when should this be used again>" steps="<step 1>|<step 2>|<step 3>"]

Most tasks do NOT deserve a skill proposal. Omit the SKILL_PROPOSAL line unless the pattern
is clearly reusable — the same 2+ tool sequence Devin (or this team) would plausibly need again.
"""

_REFLECTION_RE = re.compile(
    r'\[REFLECTION:\s*succeeded="([^"]*)"\s*worked="([^"]*)"\s*differently="([^"]*)"\]'
)
_SKILL_PROPOSAL_RE = re.compile(
    r'\[SKILL_PROPOSAL:\s*name="([^"]*)"\s*when="([^"]*)"\s*steps="([^"]*)"\]'
)


def should_reflect(tools_ran: list, denied: list) -> bool:
    """The spec's trigger: 'multi-step, or took more than one attempt'. Multi-step = 2+
    tool calls actually ran. 'Took more than one attempt' is approximated as at least one
    capability denial having occurred — the model had to run into and report an obstacle,
    not just execute cleanly on the first try. Deliberately conservative (most single-tool,
    no-friction turns don't reflect) — reflection costs an extra LLM call, and the spec's
    own examples ("3 real multi-step tasks") imply this shouldn't fire on every message."""
    return len(tools_ran or []) >= 2 or len(denied or []) >= 1


def _tools_summary(tools_ran: list) -> str:
    if not tools_ran:
        return "(none)"
    return ", ".join(step.get("tool", "?") for step in tools_ran)


def run_reflection(brain, user_message: str, tools_ran: list, final_response: str, team_key: str = None) -> dict:
    """One extra non-streaming LLM call. Returns {"reflection": {...} or None,
    "skill_proposed": Skill or None}. Never raises — a reflection failure is logged-worthy
    but must never surface as a user-facing error for a turn that already completed
    successfully; callers (server.py) run this fire-and-forget, same posture as
    _maybe_autotitle/_maybe_update_summary."""
    if brain is None or brain._brain is None:
        return {"reflection": None, "skill_proposed": None}

    prompt = _REFLECTION_PROMPT.format(
        user_message=user_message, tools_summary=_tools_summary(tools_ran), final_response=final_response,
    )
    try:
        result = brain._brain.chat([{"role": "user", "content": prompt}], "")
        text = result.get("response", "")
    except Exception:
        return {"reflection": None, "skill_proposed": None}

    reflection = None
    m = _REFLECTION_RE.search(text)
    if m:
        reflection = {"succeeded": m.group(1).lower() == "true", "worked": m.group(2), "differently": m.group(3)}

    skill_proposed = None
    sm = _SKILL_PROPOSAL_RE.search(text)
    if sm:
        name, when_to_use, steps_text = sm.group(1), sm.group(2), sm.group(3)
        steps = [s.strip() for s in steps_text.split("|") if s.strip()]
        tool_calls = list(dict.fromkeys(step.get("tool") for step in (tools_ran or []) if step.get("tool")))
        if name and steps and tool_calls:
            import skills
            try:
                skill_proposed = skills.propose_skill(name, when_to_use, steps, tool_calls, team=team_key)
            except Exception:
                skill_proposed = None

    return {"reflection": reflection, "skill_proposed": skill_proposed}

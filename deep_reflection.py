"""
deep_reflection.py — Phase 6 item 2 ("deeper self-learning", Option A, confirmed with
Devin over Option B's local fine-tuning): a periodic, slower-cadence companion to Phase
5's per-turn reflection.py. Where reflection.py only ever sees ONE turn's tools_ran (so it
can't notice "you've called check_system_health then scan_local_logs across 6 SEPARATE
single-tool turns this week" — no single turn crosses its 2-tool trigger), this scans the
accumulated episodic log across many turns for tool-pair patterns that recur but were
never called together in one turn.

Same non-negotiable as reflection.py, worth restating because this is the "broader
pattern recognition... weighted more heavily" half of the ask and it would be easy to
over-scope: this module's only side effect on the world is calling skills.propose_skill()
— exactly the same review-gated entrypoint the per-turn path uses. It does not import
approve_skill, does not import Tier, does not open authorized_targets.json, and has no
function capable of changing a tool's tier or activating anything. "Weighted more heavily
in context over time" is implemented entirely as Skill.weight()/active_skills_for_team()'s
sort order in skills.py — a presentation change, not a permissions change.

Approximation, stated plainly rather than assumed away: the episodic log (memory.py)
records one row per tool call with a timestamp, but no conversation/turn id — there was
never a need for one before this module. Turn boundaries here are inferred by time-gap
clustering (calls within `gap_seconds` of each other are treated as one interaction). This
is an approximation, not a precise turn reconstruction — good enough to notice "these two
tools keep showing up close together," not intended as an audit trail (the real audit
trail is still the episodic log itself, untouched).
"""

import os
import re
import asyncio
from collections import Counter

PATTERN_CONV_FILE = os.path.join("data", ".pattern_insights_conversation_id")
DEFAULT_SCAN_INTERVAL_SECONDS = 24 * 60 * 60  # once/day — this is a slow, background pattern scan, not a live loop

MIN_OCCURRENCES = 3       # a pattern needs to recur at least this many times to matter
LOOKBACK_EPISODES = 500   # how far back into the episodic log to look each scan
CLUSTER_GAP_SECONDS = 30  # calls within this many seconds are treated as one interaction

_PATTERN_PROMPT = """You noticed this tool combination has been used together across
{occurrences} separate interactions recently: {tools}.

Tool descriptions:
{tool_descriptions}

If this looks like a genuinely reusable pattern worth turning into a documented skill,
respond in this exact tagged format:
[PATTERN_SKILL: name="<short name>" when="<one sentence: when should this be used>" steps="<step 1>|<step 2>"]

If it doesn't look like a real pattern (coincidental, too generic, or the tools don't
actually relate to a repeatable task), respond with exactly:
[PATTERN_SKILL: none]
"""

_PATTERN_SKILL_RE = re.compile(r'\[PATTERN_SKILL:\s*name="([^"]*)"\s*when="([^"]*)"\s*steps="([^"]*)"\]')


def _cluster_episodes(episodes: list, gap_seconds: int = CLUSTER_GAP_SECONDS) -> list:
    """Groups consecutive episodic-log entries into clusters by time proximity. See module
    docstring for why this is an approximation of turn boundaries, not a precise one."""
    clusters = []
    current = []
    for ep in episodes:
        if current and ep["timestamp"] - current[-1]["timestamp"] > gap_seconds:
            clusters.append(current)
            current = []
        current.append(ep)
    if current:
        clusters.append(current)
    return clusters


def find_recurring_tool_patterns(min_occurrences: int = MIN_OCCURRENCES, lookback: int = LOOKBACK_EPISODES) -> list:
    """Returns candidate tool-pair patterns that co-occurred (within the same inferred
    interaction cluster) at least `min_occurrences` times, sorted most-frequent first.
    Read-only over the episodic log — writes nothing."""
    from memory import Memory
    m = Memory()
    episodes = m.get_recent_episodes(n=lookback)
    clusters = _cluster_episodes(episodes)

    pair_counts = Counter()
    for cluster in clusters:
        tools_in_cluster = sorted(set(ep["action"] for ep in cluster if ep.get("action")))
        if len(tools_in_cluster) < 2:
            continue  # co-occurrence needs 2+ distinct tools in the same interaction
        for i in range(len(tools_in_cluster)):
            for j in range(i + 1, len(tools_in_cluster)):
                pair_counts[(tools_in_cluster[i], tools_in_cluster[j])] += 1

    candidates = [
        {"tools": list(pair), "occurrences": count}
        for pair, count in pair_counts.items() if count >= min_occurrences
    ]
    candidates.sort(key=lambda c: -c["occurrences"])
    return candidates


def _already_has_skill_for(tool_pair: list) -> bool:
    import skills
    target = set(tool_pair)
    for skill in skills.list_skills():  # any status — don't re-propose something already proposed/active/rejected
        if target.issubset(set(skill.tools)):
            return True
    return False


def _team_for_tools(tool_names: list):
    from tools import ALL_TOOLS
    teams = [ALL_TOOLS[name].team for name in tool_names if name in ALL_TOOLS and ALL_TOOLS[name].team]
    if not teams:
        return None
    return Counter(teams).most_common(1)[0][0]


def run_deep_pattern_scan(brain) -> dict:
    """The one function server.py's background loop calls. Looks at the single strongest
    recurring pattern not already covered by an existing skill (any status — including
    ones Devin already rejected, so a rejected pattern doesn't just get re-proposed next
    scan), asks the model whether it's genuinely worth naming, and proposes a skill if so.
    Deliberately only acts on one pattern per scan — this runs on a slow interval (see
    server.py's lifespan wiring), no need to flood the review queue in one pass.

    Returns {"scanned": True, "candidates_found": int, "skill_proposed": Skill or None}.
    Never raises — same fire-and-forget safety posture as reflection.py.
    """
    try:
        candidates = find_recurring_tool_patterns()
    except Exception:
        return {"scanned": False, "candidates_found": 0, "skill_proposed": None}

    fresh_candidates = [c for c in candidates if not _already_has_skill_for(c["tools"])]
    if not fresh_candidates:
        return {"scanned": True, "candidates_found": len(candidates), "skill_proposed": None}

    top = fresh_candidates[0]
    if brain is None or brain._brain is None:
        return {"scanned": True, "candidates_found": len(candidates), "skill_proposed": None}

    from tools import ALL_TOOLS
    tool_descriptions = "\n".join(
        f"- {name}: {ALL_TOOLS[name].description}" for name in top["tools"] if name in ALL_TOOLS
    )
    prompt = _PATTERN_PROMPT.format(
        occurrences=top["occurrences"], tools=", ".join(top["tools"]), tool_descriptions=tool_descriptions,
    )
    try:
        result = brain._brain.chat([{"role": "user", "content": prompt}], "")
        text = result.get("response", "")
    except Exception:
        return {"scanned": True, "candidates_found": len(candidates), "skill_proposed": None}

    match = _PATTERN_SKILL_RE.search(text)
    if not match or match.group(1).strip().lower() == "none":
        return {"scanned": True, "candidates_found": len(candidates), "skill_proposed": None}

    name, when_to_use, steps_text = match.group(1), match.group(2), match.group(3)
    steps = [s.strip() for s in steps_text.split("|") if s.strip()]
    if not name or not steps:
        return {"scanned": True, "candidates_found": len(candidates), "skill_proposed": None}

    import skills
    skill = skills.propose_skill(name, when_to_use, steps, top["tools"], team=_team_for_tools(top["tools"]))
    return {"scanned": True, "candidates_found": len(candidates), "skill_proposed": skill}


def _get_or_create_pattern_conversation() -> str:
    from conversation_store import store
    if os.path.exists(PATTERN_CONV_FILE):
        with open(PATTERN_CONV_FILE) as f:
            cid = f.read().strip()
        if cid and store.conversation_exists(cid):
            return cid
    cid = store.create_conversation(device_id="deep-reflection", title="Pattern Insights")
    store.rename_conversation(cid, "Pattern Insights")
    os.makedirs(os.path.dirname(PATTERN_CONV_FILE) or ".", exist_ok=True)
    with open(PATTERN_CONV_FILE, "w") as f:
        f.write(cid)
    return cid


async def _run_scan_and_post(brain, broadcast_all):
    result = await asyncio.to_thread(run_deep_pattern_scan, brain)
    skill = result.get("skill_proposed")
    if not skill:
        return
    from conversation_store import store
    conv_id = _get_or_create_pattern_conversation()
    text = (
        f"Noticed a recurring pattern across {result['candidates_found']} tool-combination "
        f"candidate(s) in recent activity — proposed a new skill for your review:\n\n"
        f"**{skill.name}** ({skill.tier}) — {skill.when_to_use}\n"
        f"Tools: {', '.join(skill.tools)}\n\n"
        f"Sitting in the Skills review queue as \"proposed\" — nothing runs until you approve it."
    )
    store.add_message(conv_id, "assistant", text, source="text")
    if broadcast_all:
        await broadcast_all({
            "type": "stream_end", "conversation_id": conv_id, "full_text": text,
            "tools_ran": [], "denied": [],
        })
        await broadcast_all({"type": "conversation_list_changed"})
        await broadcast_all({"type": "skill_updated", "skill": {
            "name": skill.name, "status": skill.status, "tier": skill.tier, "team": skill.team,
            "version": skill.version, "success_count": skill.success_count, "fail_count": skill.fail_count,
            "flagged": skill.flagged, "when_to_use": skill.when_to_use, "steps": skill.steps,
            "tools": skill.tools, "created_at": skill.created_at, "updated_at": skill.updated_at,
        }})


async def deep_reflection_loop(brain, broadcast_all=None, interval_seconds: int = None):
    """Runs forever (until cancelled by server.py's lifespan on shutdown), same shape as
    posture_monitor_loop/daily_briefing_loop/team_board_dispatch_loop. Default interval is
    once/day — a slow background scan, not something that needs to run every few minutes."""
    interval = interval_seconds or int(os.getenv("JARVIS_DEEP_REFLECTION_INTERVAL_SECONDS", str(DEFAULT_SCAN_INTERVAL_SECONDS)))
    while True:
        try:
            await _run_scan_and_post(brain, broadcast_all)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[DeepReflection] scan failed: {e}")
        await asyncio.sleep(interval)

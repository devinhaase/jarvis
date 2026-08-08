"""
skills.py — Phase 5: the skill library. One human-readable markdown file per skill in
SKILLS/, a flat key:value frontmatter block (deliberately not full YAML — no new
dependency, consistent with this project's stdlib-first convention elsewhere) plus a
plain-text body. No black-box state: open any file in SKILLS/ and read exactly what
Jarvis has learned and what state it's in.

Non-negotiables enforced here, in code, not by convention:
  - propose_skill() is the ONLY way a new skill file is created, and it always writes
    status="proposed". Nothing in this module can create a skill any other way.
  - approve_skill() is the ONLY function that can set status="active". reflection.py (the
    autonomous half of this system) never calls it — see that module's docstring.
  - Tier is always computed from the skill's actual tool calls' real tiers (tools.py's
    ALL_TOOLS), recomputed at both propose and approve time — a skill can never carry a
    tier lower than what its tools genuinely require, whether by a hand-edited frontmatter
    value or a stale computation from before a tool's tier changed.
  - edit_skill() always resets status to "proposed" — an edited skill re-enters the review
    queue rather than silently keeping whatever approval it had before the edit.
  - record_skill_outcome() can set `flagged`, never `status` — a spiking failure rate is
    surfaced for Devin to look at, never auto-disabled and never silently left running
    without at least being flagged.
  - Versioned: every edit/status-change bumps `version` in the frontmatter; the file also
    lives in this project's git repo, so full history (including reverting to an old
    version) is `git log`/`git revert` on the file — no second versioning system invented.
"""

import os
import re
import time
import glob

SKILLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "SKILLS")
os.makedirs(SKILLS_DIR, exist_ok=True)

VALID_STATUSES = ("proposed", "active", "disabled", "rejected")
FAILURE_FLAG_THRESHOLD = 0.3   # flag if >30% of recent runs failed...
FAILURE_FLAG_MIN_RUNS = 5      # ...and there have been at least this many recent runs
RECENT_OUTCOMES_WINDOW = 10


def _slugify(name: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')
    return slug or "unnamed_skill"


def _path_for(name: str) -> str:
    return os.path.join(SKILLS_DIR, f"{_slugify(name)}.md")


class Skill:
    def __init__(self, name, status="proposed", tier="TIER_1", team=None, version=1,
                 created_at=None, updated_at=None, success_count=0, fail_count=0,
                 flagged=False, recent_outcomes=None, when_to_use="", steps=None, tools=None):
        self.name = name
        self.status = status
        self.tier = tier
        self.team = team
        self.version = version
        self.created_at = created_at or time.time()
        self.updated_at = updated_at or time.time()
        self.success_count = success_count
        self.fail_count = fail_count
        self.flagged = flagged
        self.recent_outcomes = recent_outcomes or []   # list of "S"/"F", newest last
        self.when_to_use = when_to_use
        self.steps = steps or []
        self.tools = tools or []

    # ------------------------------------------------------------------ serialization --
    def to_markdown(self) -> str:
        fm = [
            "---",
            f"name: {self.name}",
            f"status: {self.status}",
            f"tier: {self.tier}",
            f"team: {self.team or ''}",
            f"version: {self.version}",
            f"created_at: {self.created_at}",
            f"updated_at: {self.updated_at}",
            f"success_count: {self.success_count}",
            f"fail_count: {self.fail_count}",
            f"flagged: {str(self.flagged).lower()}",
            f"recent_outcomes: {','.join(self.recent_outcomes)}",
            f"tools: {', '.join(self.tools)}",
            "---",
            "",
            "## When to use",
            "",
            self.when_to_use.strip(),
            "",
            "## Steps",
            "",
        ]
        fm.extend(f"{i + 1}. {step}" for i, step in enumerate(self.steps))
        fm.append("")
        return "\n".join(fm)

    @classmethod
    def from_markdown(cls, text: str) -> "Skill":
        parts = text.split("---", 2)
        if len(parts) < 3:
            raise ValueError("malformed skill file — missing frontmatter block")
        fm_text, body = parts[1], parts[2]

        fields = {}
        for line in fm_text.strip().splitlines():
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()

        when_match = re.search(r'## When to use\s*\n+(.*?)(?=\n##|\Z)', body, re.S)
        steps_match = re.search(r'## Steps\s*\n+(.*?)(?=\n##|\Z)', body, re.S)
        when_to_use = when_match.group(1).strip() if when_match else ""
        steps = []
        if steps_match:
            for line in steps_match.group(1).strip().splitlines():
                m = re.match(r'\d+\.\s*(.+)', line.strip())
                if m:
                    steps.append(m.group(1))

        tools = [t.strip() for t in fields.get("tools", "").split(",") if t.strip()]
        recent = [o for o in fields.get("recent_outcomes", "").split(",") if o]

        return cls(
            name=fields.get("name", "unnamed"),
            status=fields.get("status", "proposed"),
            tier=fields.get("tier", "TIER_1"),
            team=fields.get("team") or None,
            version=int(fields.get("version", 1)),
            created_at=float(fields.get("created_at", time.time())),
            updated_at=float(fields.get("updated_at", time.time())),
            success_count=int(fields.get("success_count", 0)),
            fail_count=int(fields.get("fail_count", 0)),
            flagged=fields.get("flagged", "false").lower() == "true",
            recent_outcomes=recent,
            when_to_use=when_to_use,
            steps=steps,
            tools=tools,
        )

    def weight(self) -> float:
        """Phase 6 item 2 ('deeper self-learning', Option A — weight proven skills more
        heavily over time, still fully file-based/auditable): success_count minus
        fail_count, scaled down for skills with only a handful of runs so one lucky
        success doesn't outrank a skill genuinely proven over dozens of uses. A skill with
        zero recorded outcomes yet (freshly approved) gets weight 0 — neither promoted nor
        buried, it just hasn't earned a position yet."""
        total = self.success_count + self.fail_count
        if total == 0:
            return 0.0
        import math
        net = self.success_count - self.fail_count
        return net * math.log(total + 1)

    def as_prompt_blurb(self) -> str:
        """Short form injected into a team's system prompt (see teams.py) for an ACTIVE
        skill — a documented recipe the model can choose to follow. Following it still
        means emitting normal [TOOL: ...] calls through the normal dispatch path; this is
        a prompt hint, not a new execution primitive (see reflection.py's docstring for
        why that's the safety-relevant design choice). Proven skills (weight() clears a
        threshold) are labeled as such — visible, legible weighting, not a silent reorder."""
        steps_text = " -> ".join(self.steps) if self.steps else "(no steps recorded)"
        total_runs = self.success_count + self.fail_count
        proven_tag = f" [proven — {self.success_count}/{total_runs} successful]" if self.weight() >= 3 else ""
        return f"- \"{self.name}\"{proven_tag} (learned skill, {self.tier}): use when {self.when_to_use}. Steps: {steps_text}"


# ---------------------------------------------------------------------------
# Tier computation — always from real tool tiers, never trusted from stored data alone.
# ---------------------------------------------------------------------------

def _tier_cap_for_tools(tool_names: list) -> str:
    from tools import ALL_TOOLS, Tier
    tiers = [ALL_TOOLS[name].tier for name in tool_names if name in ALL_TOOLS]
    if not tiers:
        return Tier.TIER_1.name
    return max(tiers, key=lambda t: t.value).name


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def load_skill(name: str):
    path = _path_for(name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return Skill.from_markdown(f.read())


def save_skill(skill: Skill):
    with open(_path_for(skill.name), "w", encoding="utf-8") as f:
        f.write(skill.to_markdown())


def list_skills(status: str = None, team: str = None) -> list:
    skills = []
    for path in sorted(glob.glob(os.path.join(SKILLS_DIR, "*.md"))):
        try:
            with open(path, encoding="utf-8") as f:
                skills.append(Skill.from_markdown(f.read()))
        except Exception:
            continue  # a malformed file shouldn't take down the whole list
    if status is not None:
        skills = [s for s in skills if s.status == status]
    if team is not None:
        skills = [s for s in skills if s.team == team]
    return skills


def propose_skill(name: str, when_to_use: str, steps: list, tool_calls: list, team: str = None) -> Skill:
    """The ONLY way a new skill file is created. Always status='proposed' — see module
    docstring. If a skill with this (slugified) name already exists, this creates a new
    version proposal rather than silently overwriting an existing active skill — bumps
    version, resets status to proposed either way."""
    existing = load_skill(name)
    version = (existing.version + 1) if existing else 1
    skill = Skill(
        name=name, status="proposed", tier=_tier_cap_for_tools(tool_calls), team=team,
        version=version, when_to_use=when_to_use, steps=steps, tools=tool_calls,
    )
    save_skill(skill)
    return skill


def approve_skill(name: str):
    """The ONLY function in this codebase that can set status='active'. Recomputes the
    tier cap fresh from ALL_TOOLS before activating — defense in depth against a stale or
    hand-edited tier value, same "never trust stored data over the live source" posture
    auth_check.py/device_registry.py already apply to their own allowlists."""
    skill = load_skill(name)
    if skill is None:
        return None
    skill.tier = _tier_cap_for_tools(skill.tools)
    skill.status = "active"
    skill.updated_at = time.time()
    save_skill(skill)
    return skill


def reject_skill(name: str):
    skill = load_skill(name)
    if skill is None:
        return None
    skill.status = "rejected"
    skill.updated_at = time.time()
    save_skill(skill)
    return skill


def disable_skill(name: str):
    skill = load_skill(name)
    if skill is None:
        return None
    skill.status = "disabled"
    skill.updated_at = time.time()
    save_skill(skill)
    return skill


def edit_skill(name: str, when_to_use: str = None, steps: list = None, tool_calls: list = None):
    """Any edit resets status to 'proposed' — an edited skill re-enters the review queue
    rather than silently keeping whatever approval status it had, regardless of what
    changed. Recomputes the tier cap if tool_calls changed."""
    skill = load_skill(name)
    if skill is None:
        return None
    if when_to_use is not None:
        skill.when_to_use = when_to_use
    if steps is not None:
        skill.steps = steps
    if tool_calls is not None:
        skill.tools = tool_calls
        skill.tier = _tier_cap_for_tools(tool_calls)
    skill.status = "proposed"
    skill.version += 1
    skill.updated_at = time.time()
    save_skill(skill)
    return skill


def record_skill_outcome(name: str, success: bool):
    """Updates cumulative counters + a rolling recent-outcomes window, and sets `flagged`
    (never `status`) if the recent failure rate crosses the threshold. Called after a turn
    where the model reported following this skill (see llm.py's [SKILL_USED: name] tag)."""
    skill = load_skill(name)
    if skill is None:
        return None
    if success:
        skill.success_count += 1
    else:
        skill.fail_count += 1
    skill.recent_outcomes.append("S" if success else "F")
    skill.recent_outcomes = skill.recent_outcomes[-RECENT_OUTCOMES_WINDOW:]

    fail_rate = skill.recent_outcomes.count("F") / len(skill.recent_outcomes)
    if len(skill.recent_outcomes) >= FAILURE_FLAG_MIN_RUNS and fail_rate > FAILURE_FLAG_THRESHOLD:
        skill.flagged = True
    # Deliberately never clears flagged here even if the rate recovers — a human noticing
    # and re-reviewing is what clears a flag, not the metric quietly self-correcting.

    skill.updated_at = time.time()
    save_skill(skill)
    return skill


def clear_flag(name: str):
    """The human-review counterpart to record_skill_outcome() setting flagged=True —
    Devin looked at it, decides the skill's fine. Doesn't touch status."""
    skill = load_skill(name)
    if skill is None:
        return None
    skill.flagged = False
    skill.updated_at = time.time()
    save_skill(skill)
    return skill


def active_skills_for_team(team_key: str) -> list:
    """Active, non-flagged skills scoped to a team — flagged skills stay listed as active
    (flagging never disables) but are excluded from what gets suggested to the model going
    forward until a human clears the flag, same "don't act on your own uncertain signal"
    posture the kill switch's Tier-1-only carve-out already models elsewhere.

    Phase 6 item 2: sorted by weight() descending — a skill proven across many successful
    runs surfaces first (and gets the "[proven]" label in as_prompt_blurb), a brand-new or
    shaky one still shows up, just lower in the list. This is the entire "weighted more
    heavily in context over time" requirement — visible ordering + a visible label, nothing
    hidden, and it changes exactly one thing: presentation order in a prompt. It cannot
    change a skill's tier, cannot touch the authorization list, and cannot activate
    anything — those guarantees live in propose_skill/approve_skill/edit_skill above,
    completely untouched by this function."""
    skills = [s for s in list_skills(status="active", team=team_key) if not s.flagged]
    return sorted(skills, key=lambda s: s.weight(), reverse=True)

"""
memory_docs.py — Phase 5: USER.md and MEMORY.md, human-readable renderings of the same
semantic memory `memory.py` already persists to data/semantic_memory.json.

Same pattern Phase 8b's generate_self_knowledge.py already established for tools.py's
ALL_TOOLS: introspect live state into a readable file rather than inventing a second,
competing source of truth. semantic_memory.json stays the actual storage (already tested,
already the thing get_semantic_context() reads for the live system prompt) — USER.md/
MEMORY.md are regenerated from it on every write (cheap — a few KB, at most a couple of
writes per turn), so they're never more than one fact behind what Jarvis actually knows.

Split: USER.md is about Devin (name, preferences) — context Jarvis should already have
about who it's working for. MEMORY.md is what Jarvis has learned/accumulated (facts,
active projects) — ongoing knowledge, distinct from Devin's stable preferences.
"""

import os
from datetime import datetime, timezone

_BASE = os.path.dirname(os.path.abspath(__file__))
USER_MD_PATH = os.path.join(_BASE, "USER.md")
MEMORY_MD_PATH = os.path.join(_BASE, "MEMORY.md")


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def render_user_md(semantic_memory: dict) -> str:
    name = semantic_memory.get("user_name", "Devin")
    prefs = semantic_memory.get("preferences", {})

    lines = [
        "# USER.md — What Jarvis knows about Devin",
        "",
        f"_Auto-generated from data/semantic_memory.json — do not hand-edit, changes will be "
        f"overwritten on the next fact/preference update. Last generated: {_timestamp()}._",
        "",
        f"**Name:** {name}",
        "",
        "## Preferences",
        "",
    ]
    if prefs:
        for k, v in prefs.items():
            lines.append(f"- **{k}:** {v}")
    else:
        lines.append("_None recorded yet._")
    lines.append("")
    return "\n".join(lines)


def render_memory_md(semantic_memory: dict) -> str:
    facts = semantic_memory.get("facts", [])
    projects = semantic_memory.get("projects", [])

    lines = [
        "# MEMORY.md — What Jarvis has learned",
        "",
        f"_Auto-generated from data/semantic_memory.json — do not hand-edit, changes will be "
        f"overwritten on the next fact/preference update. Last generated: {_timestamp()}._",
        "",
        "## Ongoing facts",
        "",
    ]
    if facts:
        for fact in facts:
            lines.append(f"- {fact}")
    else:
        lines.append("_None recorded yet._")
    lines.extend(["", "## Active projects", ""])
    if projects:
        for proj in projects:
            lines.append(f"- {proj}")
    else:
        lines.append("_None recorded yet._")
    lines.append("")
    return "\n".join(lines)


def regenerate(semantic_memory: dict):
    """Writes both files. Best-effort — a failure here (e.g. a read-only filesystem)
    should never break the actual memory write that triggered it; callers wrap this in
    try/except, same convention background-task hooks elsewhere in this codebase use for
    anything that's a nice-to-have rendering, not the source of truth itself."""
    with open(USER_MD_PATH, "w", encoding="utf-8") as f:
        f.write(render_user_md(semantic_memory))
    with open(MEMORY_MD_PATH, "w", encoding="utf-8") as f:
        f.write(render_memory_md(semantic_memory))

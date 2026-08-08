"""
hacking_synthesis.py — Phase 6 item 3: Devin's own reference material (notes, past
scripts, technique writeups) in data/hacking_reference/, drawn on by the Hacking team to
synthesize new scripts for authorized testing. Confirmed with Devin before building this:
synthesis is allowed to generate genuinely new script content (not just recombine existing
tools), tightly gated — every one of the three safety properties below is enforced in
code, not by convention:

  1. Nothing synthesis produces ever auto-executes. synthesize_tool_from_reference() only
     ever writes a file to data/synthesized_tools/ (same "writes a file, never runs it"
     pattern security_modules/exploit_tools.py's generate_payload already established) and
     proposes a skill via skills.propose_skill() — always status="proposed".
  2. Approval only activates the review-queue entry. run_synthesized_script — the ONE new
     tool that can actually execute a synthesized script — is Tier 4, hardcoded in
     tools.py, unrelated to whatever tier a skill wrapping it computes to (though the tier
     cap will always land on TIER_4 regardless, since that's the highest tier among its
     one constituent tool). Tier 4 means Coordinator still asks for explicit confirmation
     on every single invocation, exactly like any other Tier-4 tool — an "active" skill
     does not skip that gate, "active" only means it's *eligible* to be suggested.
  3. run_synthesized_script re-checks the target against authorized_targets.json on every
     call via the same AuthorizationCheck every other offense tool uses — not cached from
     approval time, not skipped because it was approved once already.

The synthesis prompt itself instructs the model to stay strictly within the reference
material and stated goal, and to refuse anything destructive/self-propagating/wormable —
a persona-level guardrail, the same honest caveat this project already gives every
model-level defense (documented as probabilistic, not a guarantee, same as the
prompt-injection detector and the untrusted-content delimiters).
"""

import os
import re
import glob as _glob
from datetime import datetime, timezone

from security_hardening import run_hardened

REFERENCE_DIR = os.path.join("data", "hacking_reference")
SYNTHESIZED_DIR = os.path.join("data", "synthesized_tools")
os.makedirs(REFERENCE_DIR, exist_ok=True)
os.makedirs(SYNTHESIZED_DIR, exist_ok=True)

_SYNTHESIS_PROMPT = """You are generating a NEW script for Devin's own authorized security
testing, based on his reference material below. This script will NEVER run automatically —
it will be reviewed by Devin before being approved, and even after approval, every single
execution requires his explicit confirmation and re-checks the target against his
pre-approved authorization list. Stay strictly within what the reference material and
stated goal describe. Do not add capabilities beyond what's asked. Do not generate anything
destructive, self-propagating/wormable, or that acts on a target other than the one it's
given as an argument.

Reference material:
---
{reference_content}
---

Devin's stated goal: "{goal}"

Generate ONE self-contained script (Python or PowerShell — pick whichever fits the task)
that accomplishes this goal, written to take a target host/network as its one command-line
argument. Respond in exactly this format, nothing else:

[SYNTHESIS: name="<short tool name, lowercase, underscores>" language="<python|powershell>" description="<one sentence: what it does>"]
```
<the full script content>
```
"""

_SYNTHESIS_RE = re.compile(
    r'\[SYNTHESIS:\s*name="([^"]*)"\s*language="([^"]*)"\s*description="([^"]*)"\]\s*```\w*\n(.*?)```',
    re.S | re.I,  # live-tested against the real local model: it sometimes writes
                  # "[synthesis: ...]" lowercase despite the prompt specifying uppercase —
                  # a small-model formatting quirk, tolerate it rather than silently
                  # dropping a perfectly good synthesis result over a case mismatch
)

_EXT_FOR_LANGUAGE = {"python": ".py", "powershell": ".ps1"}
_RUNNER_FOR_EXT = {
    ".py": lambda path, target: ["python", path, target],
    ".ps1": lambda path, target: ["powershell", "-File", path, target],
    ".sh": lambda path, target: ["bash", path, target],
}


# ---------------------------------------------------------------------------
# Reference library — Tier 1/2, plain file management, nothing dangerous here.
# ---------------------------------------------------------------------------

def add_reference_material(filename: str, content: str) -> dict:
    """Tier 2. Drops a note/script/writeup into the reference library."""
    safe_name = os.path.basename(filename)
    path = os.path.join(REFERENCE_DIR, safe_name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return {"filename": safe_name, "written": True, "bytes": len(content.encode("utf-8"))}


def list_reference_material() -> dict:
    """Tier 1."""
    files = sorted(os.path.basename(p) for p in _glob.glob(os.path.join(REFERENCE_DIR, "*")) if os.path.isfile(p))
    return {"library_path": REFERENCE_DIR, "files": files}


def read_reference_material(filename: str) -> dict:
    """Tier 1."""
    safe_name = os.path.basename(filename)
    path = os.path.join(REFERENCE_DIR, safe_name)
    if not os.path.exists(path):
        return {"filename": safe_name, "error": "Not found in the reference library."}
    with open(path, encoding="utf-8", errors="replace") as f:
        return {"filename": safe_name, "content": f.read()}


# ---------------------------------------------------------------------------
# Synthesis — Tier 4. Writes a file + proposes a skill. Never executes anything.
# ---------------------------------------------------------------------------

def synthesize_tool_from_reference(reference_filename: str, goal: str) -> dict:
    """Tier 4 (explicit confirmation every call, same as generate_payload). Unlike
    team_router.py/reflection.py (orchestration code that runs *outside* a tool call and
    has the shared JarvisBrain passed to it directly), this is a plain tool function like
    any other in tools.py — no framework-level dependency injection exists for tools, so
    it builds its own short-lived LLM provider via llm.get_llm() (cheap — reads config
    from .env, no persistent connection to hold onto) rather than needing `brain` threaded
    through the whole Coordinator.run_tools() call chain just for this one tool.
    Returns the proposed skill's info, or an error — never anything that ran.
    """
    ref = read_reference_material(reference_filename)
    if "error" in ref:
        return ref

    try:
        from llm import get_llm
        provider = get_llm()
    except Exception as e:
        return {"error": f"AI brain unavailable — cannot synthesize without it: {e}"}

    prompt = _SYNTHESIS_PROMPT.format(reference_content=ref["content"][:6000], goal=goal)
    try:
        result = provider.chat([{"role": "user", "content": prompt}], "")
        text = result.get("response", "")
    except Exception as e:
        return {"error": f"Synthesis LLM call failed: {e}"}

    match = _SYNTHESIS_RE.search(text)
    if not match:
        return {"error": "Model did not produce a well-formed [SYNTHESIS: ...] response — nothing generated."}

    name, language, description, code = match.groups()
    name = name.strip() or "synthesized_tool"
    language = language.strip().lower()
    ext = _EXT_FOR_LANGUAGE.get(language)
    if not ext:
        return {"error": f"Unsupported language '{language}' — expected python or powershell."}

    slug = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_') or "synthesized_tool"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    script_filename = f"{slug}_{timestamp}{ext}"
    script_path = os.path.join(SYNTHESIZED_DIR, script_filename)
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(code.strip() + "\n")

    import skills
    proposed = skills.propose_skill(
        name=f"synth: {name}",
        when_to_use=f"{description.strip()} (goal: {goal})",
        steps=[
            f"Review the generated script at {script_path} before approving.",
            f"Run via run_synthesized_script(script_path='{script_path}', target=<authorized target>) — "
            f"Tier 4, re-checks authorization on every call regardless of approval history.",
        ],
        tool_calls=["run_synthesized_script"],
        team="hacking",
    )

    return {
        "script_path": script_path, "language": language, "description": description.strip(),
        "based_on_reference": reference_filename, "skill_proposed": proposed.name,
        "skill_status": proposed.status,  # always "proposed" — propose_skill() guarantees this
    }


# ---------------------------------------------------------------------------
# Execution — Tier 4, always. The ONLY function in this codebase that can run a
# synthesized script, and the only thing skills produced by this module ever reference.
# ---------------------------------------------------------------------------

def run_synthesized_script(script_path: str, target: str) -> dict:
    """Tier 4 — explicit confirmation required every single call, same as
    generate_payload/run_exploit_module. Re-checks `target` against
    authorized_targets.json on EVERY invocation via AuthorizationCheck — not cached, not
    skipped because a prior call (or the skill's own approval) already happened. Refuses
    to run anything outside SYNTHESIZED_DIR, so this can't be repurposed into an arbitrary-
    script runner by passing some other path."""
    from auth_check import AuthorizationCheck
    AuthorizationCheck().is_authorized(target)

    full_path = os.path.abspath(script_path)
    synthesized_root = os.path.abspath(SYNTHESIZED_DIR)
    if os.path.commonpath([synthesized_root, full_path]) != synthesized_root:
        return {"error": "Refusing to run a script outside data/synthesized_tools/ — this "
                          "tool only ever runs scripts synthesis itself produced."}
    if not os.path.exists(full_path):
        return {"error": f"Script not found: {script_path}"}

    ext = os.path.splitext(full_path)[1]
    runner = _RUNNER_FOR_EXT.get(ext)
    if not runner:
        return {"error": f"Don't know how to run a '{ext}' script."}

    cmd = runner(full_path, target)
    try:
        result = run_hardened(cmd, timeout=120)
    except Exception as e:
        return {"error": f"Execution failed: {e}"}

    return {
        "script_path": script_path, "target": target, "returncode": result.returncode,
        "stdout": (result.stdout or "")[-4000:], "stderr": (result.stderr or "")[-2000:],
    }

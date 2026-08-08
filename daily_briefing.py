"""
daily_briefing.py — The proactive half of "better AI agent": once a day, Jarvis gathers a
snapshot of your inbox, system health, and security posture, and writes itself a short
morning briefing — instead of only ever answering when asked. Runs as a background
asyncio task in server.py's lifespan, same pattern posture_monitor.py already established
(a dedicated conversation, a snapshot, a broadcast — no separate notification system).

Only runs once per real calendar day (tracked in data/.last_briefing_date). Restarting the
server mid-morning doesn't re-trigger it, and a server that's been up for days won't ever
fire twice on the same date.
"""

import os
import json
import asyncio
from datetime import datetime, date

from conversation_store import store

LAST_RUN_FILE = os.path.join("data", ".last_briefing_date")
BRIEFING_CONV_FILE = os.path.join("data", ".daily_briefing_conversation_id")
CHECK_INTERVAL_SECONDS = 5 * 60  # how often to check "is it time yet" — not how often it runs


def _briefing_hour() -> int:
    try:
        return int(os.getenv("JARVIS_BRIEFING_HOUR", "8"))
    except ValueError:
        return 8


def _load_last_run_date() -> str:
    if not os.path.exists(LAST_RUN_FILE):
        return ""
    try:
        with open(LAST_RUN_FILE, 'r') as f:
            return f.read().strip()
    except Exception:
        return ""


def _save_last_run_date(d: str):
    os.makedirs(os.path.dirname(LAST_RUN_FILE) or ".", exist_ok=True)
    with open(LAST_RUN_FILE, 'w') as f:
        f.write(d)


def _get_or_create_briefing_conversation() -> str:
    if os.path.exists(BRIEFING_CONV_FILE):
        with open(BRIEFING_CONV_FILE, 'r') as f:
            cid = f.read().strip()
        if cid and store.conversation_exists(cid):
            return cid
    cid = store.create_conversation(device_id="daily-briefing", title="Daily Briefing")
    store.rename_conversation(cid, "Daily Briefing")
    os.makedirs(os.path.dirname(BRIEFING_CONV_FILE) or ".", exist_ok=True)
    with open(BRIEFING_CONV_FILE, 'w') as f:
        f.write(cid)
    return cid


def gather_snapshot() -> dict:
    """Pulls the same tools a normal conversation would call one at a time, in one batch.
    Each failure is captured per-tool rather than aborting the whole briefing — a broken
    Gmail token shouldn't mean you also don't hear about a security posture regression."""
    from tools import ALL_TOOLS
    snapshot = {}
    for name in ("check_system_health", "get_security_posture", "read_recent_emails", "scan_local_logs"):
        tool = ALL_TOOLS.get(name)
        if not tool:
            continue
        try:
            snapshot[name] = tool.execute()
        except Exception as e:
            snapshot[name] = {"error": str(e)}
    return snapshot


def compose_briefing(brain, snapshot: dict):
    """Has the LLM turn the raw snapshot into a short, prioritized morning briefing. Returns
    None (not an error string) if the brain itself is unavailable — the caller should skip
    posting rather than post a broken placeholder, and try again on the next check."""
    if brain is None or not brain.is_available():
        return None
    prompt = (
        "Compose Devin's morning briefing from this raw data snapshot. Touch on every "
        "category below that has data — don't fixate on just one and ignore the rest. "
        "Be concise (5-8 sentences total across ALL categories), lead with anything that "
        "actually needs attention, and give routine/nothing-wrong categories one short "
        "clause each rather than skipping them silently or over-elaborating.\n\n"
        "For emails specifically: treat promotional/marketing mail (workshop invites, "
        "sales pitches, newsletters, cold outreach) as noise, not action items — do not "
        "suggest replying to or acting on it. Only surface an email if it's genuinely "
        "time-sensitive or from someone who needs a real reply.\n\n"
        f"DATA:\n{json.dumps(snapshot, indent=2, default=str)[:6000]}"
    )
    try:
        from llm import stream_and_filter_tags
        raw = "".join(brain._brain.chat_stream([{"role": "user", "content": prompt}], brain.build_prompt()))
        return "".join(stream_and_filter_tags([raw])).strip()
    except Exception as e:
        return f"(Briefing generation failed: {e})"


async def _maybe_run_briefing(brain, broadcast_all):
    today = date.today().isoformat()
    if _load_last_run_date() == today:
        return
    if datetime.now().hour < _briefing_hour():
        return

    snapshot = await asyncio.to_thread(gather_snapshot)
    text = await asyncio.to_thread(compose_briefing, brain, snapshot)
    if not text:
        return  # brain unavailable right now — retry on the next check, don't mark today done

    conv_id = _get_or_create_briefing_conversation()
    store.add_message(conv_id, "assistant", text, source="text")
    _save_last_run_date(today)

    if broadcast_all:
        await broadcast_all({
            "type": "stream_end", "conversation_id": conv_id,
            "full_text": text, "tools_ran": [], "denied": [],
        })
        await broadcast_all({"type": "conversation_list_changed"})

    # Phase 6 item 6: "briefing" category.
    try:
        import push_notifications as _push
        await asyncio.to_thread(_push.send_to_all, "briefing", "Jarvis: morning briefing", text,
                                 tag="briefing", conversation_id=conv_id)
    except Exception:
        pass


async def daily_briefing_loop(brain=None, broadcast_all=None):
    """Runs forever until cancelled — checks every CHECK_INTERVAL_SECONDS whether it's past
    the configured hour (JARVIS_BRIEFING_HOUR, default 8) and hasn't run yet today."""
    while True:
        try:
            await _maybe_run_briefing(brain, broadcast_all)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[DailyBriefing] check failed: {e}")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)

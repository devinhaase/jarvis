"""
observer.py — the unifying urgency layer promised alongside vision as one of the two
"make Jarvis itself better" priorities (see task.md/MEMORY.md's research-session entry):
network_monitor.py, posture_monitor.py, and uptime_kuma_monitor.py each already detect
real anomalies well (network_monitor's statistical baseline in particular), but each one
pushes to your phone for EVERY finding, every time, with no memory of "this exact kind of
thing already happened three times this week." That's the actual gap versus a genuinely
proactive assistant: not detection, but knowing what's actually worth interrupting you for.

Design, deliberately simple rather than a new ML layer: every finding from every monitor
gets logged to one shared append-only event log, keyed by (source, category) — the same
category granularity push_notifications.py's toggles already use, not fuzzy text matching,
since that's already the right level of "kind of thing" (a monitor's category is set by the
finding's own type, not its exact wording). If the same (source, category) has fired
REPEAT_THRESHOLD or more times in the trailing REPEAT_WINDOW_DAYS, it's "routine" — still
recorded, still posted into that monitor's own conversation for the full record, but the
push notification (the thing that actually interrupts you) is skipped, and it rolls into
the daily briefing's digest instead. First-time-ever findings are always "novel" — nothing
is suppressed on a cold start, same "don't cry wolf until there's actually a pattern"
posture the monitors themselves already use for their own first-run baselines.

Never raises: a broken event log should degrade to "treat everything as novel" (the safe
direction — you'd rather get one extra push than silently miss something new), never crash
a monitor loop that's calling in.
"""

import os
import json
import time

EVENTS_FILE = os.path.join("data", "observer_events.jsonl")

REPEAT_WINDOW_DAYS = 7
REPEAT_THRESHOLD = 3
# Bounds how far back the log is scanned/kept — well past REPEAT_WINDOW_DAYS so a slow
# week doesn't lose the tail of its own window, but not unbounded (this file is read in
# full on every classification call; a year of history isn't needed for a 7-day window).
RETENTION_DAYS = 30


def _now() -> float:
    return time.time()


def _read_events() -> list:
    if not os.path.exists(EVENTS_FILE):
        return []
    events = []
    try:
        with open(EVENTS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except Exception:
                    continue  # one corrupt line shouldn't lose the rest of the log
    except Exception:
        return []
    return events


def _prune_and_rewrite(events: list):
    """Drops anything older than RETENTION_DAYS and rewrites the file — called opportunistically
    from record() rather than on a separate timer, so the file never needs its own background
    loop just to stay bounded."""
    cutoff = _now() - RETENTION_DAYS * 86400
    kept = [e for e in events if e.get("ts", 0) >= cutoff]
    try:
        os.makedirs(os.path.dirname(EVENTS_FILE) or ".", exist_ok=True)
        with open(EVENTS_FILE, "w", encoding="utf-8") as f:
            for e in kept:
                f.write(json.dumps(e) + "\n")
    except Exception:
        pass  # best-effort housekeeping; a failed rewrite just means the file grows a bit more
    return kept


def _append(event: dict):
    try:
        os.makedirs(os.path.dirname(EVENTS_FILE) or ".", exist_ok=True)
        with open(EVENTS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
    except Exception:
        pass


def record_and_classify(source: str, category: str, text: str) -> str:
    """Call this once per finding, right after a monitor decides something IS worth
    recording (i.e. after its own diff already found something) but BEFORE deciding
    whether to send a push notification. Returns "novel" or "routine" — a monitor should
    keep posting to its own conversation either way (that's the full, honest record), but
    only send a push notification on "novel".

    source: which monitor ("network", "posture", "uptime_kuma", ...) — a free-form string,
    not required to match push_notifications.py's category names, since a monitor may fold
    several push categories under one source. category: the specific kind of finding
    within that source (e.g. network_monitor's own _category_for() result) — this is the
    actual dedup key, paired with source. text: the human-readable finding, recorded for
    the daily-briefing digest, never used for matching.
    """
    now = _now()
    events = _read_events()
    events = _prune_and_rewrite(events) if len(events) > 500 else events  # avoid rewriting every single call

    window_start = now - REPEAT_WINDOW_DAYS * 86400
    prior_count = sum(
        1 for e in events
        if e.get("source") == source and e.get("category") == category and e.get("ts", 0) >= window_start
    )

    urgency = "routine" if prior_count + 1 >= REPEAT_THRESHOLD else "novel"

    _append({
        "ts": now, "source": source, "category": category, "text": text, "urgency": urgency,
    })
    return urgency


def get_routine_digest(hours: int = 24) -> str:
    """For daily_briefing.py: a short summary of what got auto-suppressed in the trailing
    window, grouped by (source, category), so routine-but-real activity still surfaces
    once a day instead of vanishing entirely. Empty string if nothing was suppressed —
    gather_snapshot() only adds this to the briefing data when there's something to say."""
    events = _read_events()
    window_start = _now() - hours * 3600
    routine = [e for e in events if e.get("urgency") == "routine" and e.get("ts", 0) >= window_start]
    if not routine:
        return ""

    counts = {}
    for e in routine:
        key = (e.get("source", "?"), e.get("category", "?"))
        counts[key] = counts.get(key, 0) + 1

    lines = [
        f"- {source}/{category}: {count}x (recurring, no push sent)"
        for (source, category), count in sorted(counts.items())
    ]
    return "Auto-suppressed routine events in the last day (already recurring, not new):\n" + "\n".join(lines)

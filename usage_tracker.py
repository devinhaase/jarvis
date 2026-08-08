"""
usage_tracker.py — Approximate token/cost tracking across turns. Irrelevant on Ollama
(always $0, always local) but gives real visibility the moment ACTIVE_LLM points at a paid
API — right now there's zero tracking of that, so flipping providers in .env would mean
flying blind on spend.

Token counts are an approximation (len(text) / 4, the same rough heuristic OpenAI's own
docs use as a rule of thumb for English text) — not exact, no tokenizer dependency added
just for this. Good enough for "roughly how much am I spending", not for a billing dispute.
"""

import os
import json
import time
import threading
from datetime import date

USAGE_FILE = os.path.join("data", "usage_stats.json")
_lock = threading.Lock()

# USD per 1,000,000 tokens: (input, output). Ollama is always free — it's your own hardware.
# These are illustrative, not live-fetched — provider pricing changes; treat as directional.
PRICING = {
    "openai": {"gpt-4o": (2.50, 10.00)},
    "anthropic": {"claude-3-5-sonnet-20240620": (3.00, 15.00)},
    "gemini": {"gemini-1.5-flash": (0.075, 0.30)},
    "ollama": {"*": (0.0, 0.0)},
}


def _model_for_provider(provider: str) -> str:
    """Mirrors the model strings actually hardcoded in each LLMProvider subclass in
    llm.py — kept here rather than making every provider expose a .model attribute, since
    this is the only place that needs to know it."""
    if provider == "ollama":
        return os.getenv("OLLAMA_MODEL", "llama3")
    if provider == "openai":
        return "gpt-4o"
    if provider == "anthropic":
        return "claude-3-5-sonnet-20240620"
    if provider == "gemini":
        return "gemini-1.5-flash"
    return "unknown"


def estimate_tokens(text: str) -> int:
    return max(0, len(text or "")) // 4


def _rate_for(provider: str, model: str):
    table = PRICING.get(provider, {})
    return table.get(model, table.get("*", (0.0, 0.0)))


def record_usage(provider: str, input_text: str, output_text: str, model: str = None) -> dict:
    """Estimates and persists token/cost usage for one LLM call. Returns the entry that was
    recorded. Never raises — a tracking failure shouldn't take down the actual chat turn
    that triggered it."""
    try:
        model = model or _model_for_provider(provider)
        input_tokens = estimate_tokens(input_text)
        output_tokens = estimate_tokens(output_text)
        rate_in, rate_out = _rate_for(provider, model)
        cost = (input_tokens / 1_000_000) * rate_in + (output_tokens / 1_000_000) * rate_out

        today = date.today().isoformat()
        with _lock:
            stats = _load()
            day = stats.setdefault(today, {})
            entry = day.setdefault(provider, {
                "model": model, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "calls": 0
            })
            entry["input_tokens"] += input_tokens
            entry["output_tokens"] += output_tokens
            entry["cost_usd"] = round(entry["cost_usd"] + cost, 6)
            entry["calls"] += 1
            entry["model"] = model  # keep current in case ACTIVE_LLM's model choice changed mid-day
            _save(stats)
        return {"date": today, "provider": provider, "model": model,
                "input_tokens": input_tokens, "output_tokens": output_tokens, "cost_usd": round(cost, 6)}
    except Exception as e:
        print(f"[UsageTracker] failed to record usage: {e}")
        return {}


def _load() -> dict:
    if not os.path.exists(USAGE_FILE):
        return {}
    try:
        with open(USAGE_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def _save(stats: dict):
    os.makedirs(os.path.dirname(USAGE_FILE) or ".", exist_ok=True)
    with open(USAGE_FILE, 'w') as f:
        json.dump(stats, f, indent=2)


def get_usage_stats(days: int = 30) -> dict:
    """Aggregated usage over the last `days` calendar days (by date string, not a rolling
    24h window) — total cost, per-provider totals, and the raw daily breakdown."""
    stats = _load()
    if not stats:
        return {"days": days, "total_cost_usd": 0.0, "by_provider": {}, "daily": {}}

    cutoff = time.time() - days * 86400
    relevant = {}
    for day_str, providers in stats.items():
        try:
            day_ts = time.mktime(time.strptime(day_str, "%Y-%m-%d"))
        except ValueError:
            continue
        if day_ts >= cutoff:
            relevant[day_str] = providers

    by_provider = {}
    total_cost = 0.0
    for providers in relevant.values():
        for provider, entry in providers.items():
            agg = by_provider.setdefault(provider, {
                "model": entry.get("model"), "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "calls": 0
            })
            agg["input_tokens"] += entry.get("input_tokens", 0)
            agg["output_tokens"] += entry.get("output_tokens", 0)
            agg["cost_usd"] = round(agg["cost_usd"] + entry.get("cost_usd", 0.0), 6)
            agg["calls"] += entry.get("calls", 0)
            total_cost += entry.get("cost_usd", 0.0)

    return {
        "days": days, "total_cost_usd": round(total_cost, 6),
        "by_provider": by_provider, "daily": relevant,
        "note": "Token counts are an approximation (chars/4), not an exact tokenizer count.",
    }

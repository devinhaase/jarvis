"""
Tests for Phase 3c: streaming + tag-based tool-call protocol in llm.py.

Sections 1-6 are pure unit tests of the tag-filtering state machine (no network).
Section 7 is a live smoke test against the real local Ollama instance (this repo's
ACTIVE_LLM), the same "at least one real end-to-end check" pattern used by the other
Phase 2 test suites — skipped gracefully if Ollama isn't reachable rather than failing.

Run: python test_llm_streaming_phase3c.py
"""

import sys
import os

PASS = 0
FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}  {detail}")


def section(title):
    print(f"\n=== {title} ===")


from llm import stream_and_filter_tags, parse_tagged_response, TOOL_TAG_RE, REMEMBER_TAG_RE

# ---------------------------------------------------------------------------
section("1. Plain text passthrough — no tags at all")
# ---------------------------------------------------------------------------
chunks = ["Your CPU's ", "fine, ", "nothing else needed here."]
out = "".join(stream_and_filter_tags(chunks))
check("plain text streams through unchanged", out == "Your CPU's fine, nothing else needed here.",
      f"got: {out!r}")

# ---------------------------------------------------------------------------
section("2. TOOL tag in a single chunk")
# ---------------------------------------------------------------------------
seen_tools = []
chunks = ['[TOOL: check_system_health {}]']
out = "".join(stream_and_filter_tags(chunks, on_tool=lambda m: seen_tools.append(m.group(1))))
check("tool tag fully stripped from visible output", out == "", f"got: {out!r}")
check("on_tool fired once with the right tool name", seen_tools == ["check_system_health"],
      f"got: {seen_tools}")

# ---------------------------------------------------------------------------
section("3. TOOL tag with args, split across many tiny chunks (real streaming)")
# ---------------------------------------------------------------------------
full_text = 'Checking that now. [TOOL: scan_ports {"target": "127.0.0.1", "port_range": "1-100"}] '
captured = []
out = "".join(stream_and_filter_tags(
    list(full_text),  # one character at a time — worst case for the buffering logic
    on_tool=lambda m: captured.append(m.group(0)),
))
check("visible text around the tag survives char-by-char streaming",
      out == "Checking that now.  ", f"got: {out!r}")
check("tag captured exactly once even when split into single characters",
      len(captured) == 1 and "scan_ports" in captured[0], f"got: {captured}")

# verify the args actually parse right by round-tripping through the real callback shape
from llm import _tool_call_from_match
m = TOOL_TAG_RE.fullmatch(captured[0])
check("split tag still regex-matches cleanly once reassembled", m is not None)
call = _tool_call_from_match(m)
check("args parsed correctly", call == {"tool": "scan_ports", "args": {"target": "127.0.0.1", "port_range": "1-100"}},
      f"got: {call}")

# ---------------------------------------------------------------------------
section("4. Multiple TOOL tags in one round")
# ---------------------------------------------------------------------------
seen = []
chunks = ['[TOOL: lookup_cve {"search_term": "apache 2.4"}]\n[TOOL: run_privesc_enum {}]']
out = "".join(stream_and_filter_tags(chunks, on_tool=lambda m: seen.append(m.group(1))))
check("both tool tags extracted", seen == ["lookup_cve", "run_privesc_enum"], f"got: {seen}")
check("only the newline between them remains visible", out == "\n", f"got: {out!r}")

# ---------------------------------------------------------------------------
section("5. REMEMBER tags — left in place unless a callback is given")
# ---------------------------------------------------------------------------
text = "Noted. [REMEMBER: Devin prefers dark mode.] Anything else?"
out_no_cb = "".join(stream_and_filter_tags([text]))
check("REMEMBER tag left untouched when on_remember=None (chat() compatibility path)",
      "[REMEMBER: Devin prefers dark mode.]" in out_no_cb, f"got: {out_no_cb!r}")

facts = []
out_with_cb = "".join(stream_and_filter_tags([text], on_remember=lambda m: facts.append(m.group(1))))
check("REMEMBER tag stripped and captured when a callback is given",
      "[REMEMBER" not in out_with_cb and facts == ["Devin prefers dark mode."],
      f"out={out_with_cb!r} facts={facts}")

# ---------------------------------------------------------------------------
section("6. Malformed / edge-case input doesn't crash")
# ---------------------------------------------------------------------------
try:
    out = "".join(stream_and_filter_tags(['[TOOL: bad_json {not valid json}]']))
    check("malformed tool JSON args don't raise (falls back to a plain-text bracket)", True)
except Exception as e:
    check("malformed tool JSON args don't raise", False, f"raised: {e}")

# a literal, unrelated bracket in normal prose (e.g. a citation) should just pass through
out = "".join(stream_and_filter_tags(["See reference [1] for details."]))
check("an ordinary bracketed number in prose passes through untouched",
      out == "See reference [1] for details.", f"got: {out!r}")

# an unclosed bracket at end-of-stream should still be flushed, not silently dropped
out = "".join(stream_and_filter_tags(["trailing text ["]))
check("an unclosed trailing bracket is flushed at end of stream, not lost",
      out == "trailing text [", f"got: {out!r}")

check("parse_tagged_response() end-to-end",
      parse_tagged_response('[TOOL: check_system_health {}]Result pending.') ==
      {"response": "Result pending.", "tools": [{"tool": "check_system_health", "args": {}}]})

# ---------------------------------------------------------------------------
section("7. Live smoke test against the real local Ollama instance (best-effort)")
# ---------------------------------------------------------------------------
try:
    import requests as _r
    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    _r.get(ollama_url, timeout=2)
    ollama_up = True
except Exception:
    ollama_up = False

if not ollama_up:
    print("  [SKIP] Ollama not reachable at OLLAMA_URL — skipping live streaming check.")
else:
    from llm import OllamaLlm
    provider = OllamaLlm()
    chunks_received = []
    try:
        for chunk in provider.chat_stream(
            [{"role": "user", "content": "Reply with exactly the word: online"}],
            "You are a terse test assistant. Reply in plain text only, no tags, no tools."
        ):
            chunks_received.append(chunk)
        full = "".join(chunks_received)
        check("Ollama chat_stream yields at least one chunk", len(chunks_received) >= 1,
              f"got {len(chunks_received)} chunks")
        check("Ollama chat_stream produced non-empty text", len(full.strip()) > 0, f"got: {full!r}")
    except Exception as e:
        check("Ollama live streaming call completes without raising", False, f"raised: {e}")

print(f"\n{'=' * 55}\n{PASS} passed, {FAIL} failed\n{'=' * 55}")
sys.exit(1 if FAIL else 0)

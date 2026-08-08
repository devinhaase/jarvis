"""
Tests for Phase 3c: JarvisBrain.process_turn_stream() — the streaming ReAct loop used by
the web GUI and voice companion.

Uses a FakeProvider (queued canned chat_stream() responses) instead of a real LLM, so this
is deterministic and doesn't depend on network/Ollama being up. Uses an isolated temp
Memory dir throughout — never touches real data/.

Run: python test_session_manager_streaming_phase3c.py
"""

import sys
import os
import shutil
import tempfile

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


from session_manager import JarvisBrain
from memory import Memory

TMP_DIR = tempfile.mkdtemp(prefix="jarvis_test_streaming_")


class FakeProvider:
    """Swaps in for a real LLMProvider — chat_stream() pops the next canned response
    (a list of chunk strings) off a queue each call, one call per ReAct round."""
    def __init__(self, rounds):
        self._rounds = list(rounds)
        self.calls = 0

    def chat_stream(self, messages, system_prompt):
        self.calls += 1
        if not self._rounds:
            return iter(["(no more canned rounds)"])
        return iter(self._rounds.pop(0))


def make_brain(rounds, approval_fn=None):
    mem = Memory(data_dir=os.path.join(TMP_DIR, f"mem_{len(rounds)}_{id(rounds)}"))
    brain = JarvisBrain(approval_fn=approval_fn or (lambda action, tier: True), memory=mem)
    brain._brain = FakeProvider(rounds)
    brain._llm_error = None
    return brain


def drain(gen):
    events = list(gen)
    return events


# ---------------------------------------------------------------------------
section("1. Plain no-tool turn streams chunks then a matching done event")
# ---------------------------------------------------------------------------
brain = make_brain([["Hello ", "there, ", "Devin."]])
history = [{"role": "user", "content": "hi"}]
events = drain(brain.process_turn_stream(history))

chunk_events = [e for e in events if e[0] == "chunk"]
round_ends = [e for e in events if e[0] == "round_end"]
done = [e for e in events if e[0] == "done"]

check("got the expected chunk sequence", [c[1] for c in chunk_events] == ["Hello ", "there, ", "Devin."],
      f"got: {chunk_events}")
check("exactly one round_end with no tools", round_ends == [("round_end", {"tools_ran": []})], f"got: {round_ends}")
check("exactly one done event", len(done) == 1)
check("done.response matches the joined chunks", done[0][1]["response"] == "Hello there, Devin.",
      f"got: {done[0][1]}")
check("done.tools_ran is empty", done[0][1]["tools_ran"] == [])
check("chat_history got the assistant turn appended", history[-1] == {"role": "assistant", "content": "Hello there, Devin."},
      f"got: {history[-1]}")

# ---------------------------------------------------------------------------
section("2. Tool round -> final round, two full LLM calls")
# ---------------------------------------------------------------------------
statuses = []
brain = make_brain([
    ['[TOOL: check_system_health {}]'],
    ["CPU's fine."],
])
history = [{"role": "user", "content": "how's my cpu"}]
events = drain(brain.process_turn_stream(history, on_status=lambda ev, data: statuses.append((ev, data))))

check("provider got called twice (one per round)", brain._brain.calls == 2)
check("on_status fired for tools_starting with the right tool name",
      statuses == [("tools_starting", ["check_system_health"])], f"got: {statuses}")
round_ends = [e for e in events if e[0] == "round_end"]
check("first round_end reports the tool that ran", round_ends[0] == ("round_end", {"tools_ran": ["check_system_health"]}),
      f"got: {round_ends}")
check("second round_end reports no tools (final answer round)", round_ends[1] == ("round_end", {"tools_ran": []}))
done = [e for e in events if e[0] == "done"][0][1]
check("final response is the second round's text", done["response"] == "CPU's fine.", f"got: {done}")
check("tools_ran lists the executed tool call", done["tools_ran"] == [{"tool": "check_system_health", "args": {}}],
      f"got: {done['tools_ran']}")
check("the tool's [TOOL: ...] tag never leaked into any chunk",
      not any("[TOOL" in e[1] for e in events if e[0] == "chunk"))

# ---------------------------------------------------------------------------
section("3. Capability filtering blocks a tool the device didn't declare")
# ---------------------------------------------------------------------------
brain = make_brain([
    ['[TOOL: check_system_health {}]'],
    ["Couldn't check — but here's what I know otherwise."],
])
history = [{"role": "user", "content": "cpu?"}]
events = drain(brain.process_turn_stream(history, device_capabilities=[]))  # no 'filesystem' cap
done = [e for e in events if e[0] == "done"][0][1]
check("denied list reports the blocked tool", done["denied"] == [{"tool": "check_system_health", "required": "filesystem"}],
      f"got: {done['denied']}")
check("tools_ran is empty since the only requested tool was blocked", done["tools_ran"] == [])

# ---------------------------------------------------------------------------
section("4. REMEMBER tags persist to memory during streaming, never shown")
# ---------------------------------------------------------------------------
brain = make_brain([["Got it. [REMEMBER: Devin's favorite color is blue.] Anything else?"]])
history = [{"role": "user", "content": "my favorite color is blue"}]
events = drain(brain.process_turn_stream(history))
done = [e for e in events if e[0] == "done"][0][1]
check("REMEMBER tag stripped from the visible response",
      "[REMEMBER" not in done["response"], f"got: {done['response']!r}")
check("fact actually persisted to memory",
      "Devin's favorite color is blue." in brain.memory.semantic_memory.get("facts", []),
      f"got: {brain.memory.semantic_memory.get('facts')}")

# ---------------------------------------------------------------------------
section("5. Brain unavailable — yields an explanatory chunk + done, doesn't crash")
# ---------------------------------------------------------------------------
mem = Memory(data_dir=os.path.join(TMP_DIR, "mem_unavailable"))
brain = JarvisBrain(approval_fn=lambda a, t: True, memory=mem)
brain._brain = None
brain._llm_error = "no API key configured"
events = drain(brain.process_turn_stream([{"role": "user", "content": "hi"}]))
check("exactly one chunk and one done when brain is unavailable", len(events) == 2, f"got: {events}")
check("done.response explains the brain is unavailable",
      "unavailable" in events[-1][1]["response"].lower(), f"got: {events[-1]}")

# ---------------------------------------------------------------------------
section("6. max_iterations exhausted (model keeps requesting tools forever)")
# ---------------------------------------------------------------------------
brain = make_brain([
    ['[TOOL: check_system_health {}]'],
    ['[TOOL: check_system_health {}]'],
    ['[TOOL: check_system_health {}]'],
])
events = drain(brain.process_turn_stream([{"role": "user", "content": "loop"}], max_iterations=3))
check("provider called exactly max_iterations times, then gives up", brain._brain.calls == 3)
done = [e for e in events if e[0] == "done"][0][1]
check("done.response is empty — no final answer was ever produced", done["response"] == "", f"got: {done}")
check("all three tool rounds are recorded in tools_ran", len(done["tools_ran"]) == 3)

# ---------------------------------------------------------------------------
shutil.rmtree(TMP_DIR, ignore_errors=True)

print(f"\n{'=' * 55}\n{PASS} passed, {FAIL} failed\n{'=' * 55}")
sys.exit(1 if FAIL else 0)

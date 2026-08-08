"""
Tests for Phase 7: LLM fallback provider — llm.py's FallbackLLM + get_llm() wiring.

Run: python test_llm_fallback_phase7.py
"""

import sys
import unittest.mock as mock

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


from llm import FallbackLLM, get_llm, OllamaLlm, AnthropicLlm

# ---------------------------------------------------------------------------
section("1. No fallback configured — behaves exactly like the primary alone")
# ---------------------------------------------------------------------------
class WorkingProvider:
    def chat_stream(self, messages, system_prompt):
        return iter(["Hello ", "there."])

llm = FallbackLLM(WorkingProvider(), fallback=None)
result = "".join(llm.chat_stream([], "sys"))
check("with no fallback set, just passes through the primary's output", result == "Hello there.")

# ---------------------------------------------------------------------------
section("2. Primary works fine — fallback never even considered")
# ---------------------------------------------------------------------------
class FailingIfCalledProvider:
    def chat_stream(self, messages, system_prompt):
        raise AssertionError("fallback should never have been invoked — primary was healthy")

llm = FallbackLLM(WorkingProvider(), fallback=FailingIfCalledProvider(), fallback_name="test-fallback")
result = "".join(llm.chat_stream([], "sys"))
check("primary succeeding never touches the fallback at all", result == "Hello there.")

# ---------------------------------------------------------------------------
section("3. Primary fails before producing any output — falls back cleanly")
# ---------------------------------------------------------------------------
class ConnectionRefusedProvider:
    def chat_stream(self, messages, system_prompt):
        raise ConnectionError("Connection refused")
        yield  # pragma: no cover — unreachable, makes this a generator function

class FallbackWorks:
    def chat_stream(self, messages, system_prompt):
        return iter(["Fallback ", "response."])

llm = FallbackLLM(ConnectionRefusedProvider(), fallback=FallbackWorks(), fallback_name="test-fallback")
result = "".join(llm.chat_stream([], "sys"))
check("a connection failure on the primary falls all the way through to the fallback's real output",
      result == "Fallback response.", f"got: {result!r}")

# ---------------------------------------------------------------------------
section("4. Primary starts fine, breaks mid-stream — does NOT splice in the fallback")
# ---------------------------------------------------------------------------
class BreaksMidStreamProvider:
    def chat_stream(self, messages, system_prompt):
        yield "Partial answer, "
        raise TimeoutError("connection dropped mid-response")

llm = FallbackLLM(BreaksMidStreamProvider(), fallback=FailingIfCalledProvider(), fallback_name="test-fallback")
try:
    result = "".join(llm.chat_stream([], "sys"))
    check("a mid-stream failure propagates as a real error, not silently patched over", False,
          f"expected an exception, got clean result: {result!r}")
except TimeoutError:
    check("a mid-stream failure propagates as a real error (not silently spliced with a fallback)", True)
except AssertionError as e:
    check("a mid-stream failure propagates as a real error, not silently patched over", False, str(e))

# ---------------------------------------------------------------------------
section("5. Primary produces zero output (not an error) — no fallback triggered")
# ---------------------------------------------------------------------------
class EmptyProvider:
    def chat_stream(self, messages, system_prompt):
        return iter([])

llm = FallbackLLM(EmptyProvider(), fallback=FailingIfCalledProvider(), fallback_name="test-fallback")
result = "".join(llm.chat_stream([], "sys"))
check("an empty (but not erroring) primary response doesn't trigger the fallback either",
      result == "")

# ---------------------------------------------------------------------------
section("6. get_llm() wiring — only wraps in FallbackLLM when actually configured")
# ---------------------------------------------------------------------------
with mock.patch.dict("os.environ", {"ACTIVE_LLM": "ollama"}, clear=False):
    import os as _os
    _os.environ.pop("ACTIVE_LLM_FALLBACK", None)
    brain = get_llm()
    check("no ACTIVE_LLM_FALLBACK set: get_llm() returns the bare primary, not a FallbackLLM wrapper",
          isinstance(brain, OllamaLlm) and not isinstance(brain, FallbackLLM), f"got: {type(brain)}")

with mock.patch.dict("os.environ", {"ACTIVE_LLM": "ollama", "ACTIVE_LLM_FALLBACK": "anthropic"}):
    brain = get_llm()
    check("ACTIVE_LLM_FALLBACK set and different from primary: wraps in FallbackLLM",
          isinstance(brain, FallbackLLM), f"got: {type(brain)}")
    check("the wrapped primary is the configured ACTIVE_LLM provider",
          isinstance(brain._primary, OllamaLlm), f"got: {type(brain._primary)}")
    check("the configured fallback provider matches ACTIVE_LLM_FALLBACK",
          isinstance(brain._fallback, AnthropicLlm), f"got: {type(brain._fallback)}")

with mock.patch.dict("os.environ", {"ACTIVE_LLM": "ollama", "ACTIVE_LLM_FALLBACK": "ollama"}):
    brain = get_llm()
    check("fallback set to the SAME provider as primary is a no-op (pointless to fall back to yourself)",
          not isinstance(brain, FallbackLLM), f"got: {type(brain)}")


print(f"\n{'=' * 55}\n{PASS} passed, {FAIL} failed\n{'=' * 55}")
sys.exit(1 if FAIL else 0)

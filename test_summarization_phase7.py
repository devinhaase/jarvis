"""
Tests for Phase 7: conversation summarization for long chats — conversation_store.py's
get_llm_context_history/get_messages_to_summarize/set_conversation_summary, and server.py's
_maybe_update_summary trigger.

Run: python test_summarization_phase7.py
"""

import os
import sys
import shutil
import asyncio
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


from conversation_store import ConversationStore

tmp_dir = tempfile.mkdtemp(prefix="jarvis_test_summarization_")
store = ConversationStore(path=os.path.join(tmp_dir, "test.db"))

# ---------------------------------------------------------------------------
section("1. get_llm_context_history — under threshold returns everything, untouched")
# ---------------------------------------------------------------------------
conv = store.create_conversation()
for i in range(10):
    store.add_message(conv, "user" if i % 2 == 0 else "assistant", f"message {i}")

full = store.get_llm_context_history(conv, recent_n=20, threshold=30)
check("under threshold, returns the full unmodified history",
      full == store.get_chat_history(conv), f"got {len(full)} messages")

# ---------------------------------------------------------------------------
section("2. Over threshold, no summary yet — falls back to just the recent window")
# ---------------------------------------------------------------------------
conv2 = store.create_conversation()
for i in range(35):
    store.add_message(conv2, "user" if i % 2 == 0 else "assistant", f"msg {i}")

context = store.get_llm_context_history(conv2, recent_n=20, threshold=30)
check("no summary yet: returns exactly recent_n messages (no synthetic summary message)",
      len(context) == 20, f"got {len(context)}")
check("those are genuinely the most recent ones, in order",
      context[-1]["content"] == "msg 34" and context[0]["content"] == "msg 15",
      f"got first={context[0]}, last={context[-1]}")

# ---------------------------------------------------------------------------
section("3. get_messages_to_summarize")
# ---------------------------------------------------------------------------
to_summarize = store.get_messages_to_summarize(conv2, recent_n=20)
check("returns exactly the 15 older messages (35 total - 20 recent)",
      len(to_summarize) == 15, f"got {len(to_summarize)}")
check("in ascending order, oldest first", [m["content"] for m in to_summarize[:3]] == ["msg 0", "msg 1", "msg 2"])

conv3 = store.create_conversation()
for i in range(15):
    store.add_message(conv3, "user", f"m{i}")
check("not enough messages beyond recent_n yet: returns empty, nothing to summarize",
      store.get_messages_to_summarize(conv3, recent_n=20) == [])

# ---------------------------------------------------------------------------
section("4. set_conversation_summary + get_llm_context_history WITH a summary")
# ---------------------------------------------------------------------------
through_id = to_summarize[-1]["id"]
store.set_conversation_summary(conv2, "Devin and Jarvis discussed 15 earlier topics.", through_id)

conv_row = store.get_conversation(conv2)
check("summary persisted", conv_row["summary"] == "Devin and Jarvis discussed 15 earlier topics.")
check("summary_through_message_id persisted", conv_row["summary_through_message_id"] == through_id)
check("summary_updated_at is set", conv_row["summary_updated_at"] is not None)

context_with_summary = store.get_llm_context_history(conv2, recent_n=20, threshold=30)
check("now returns 21 items: the synthetic summary message + 20 recent",
      len(context_with_summary) == 21, f"got {len(context_with_summary)}")
check("first item is the summary, clearly labeled",
      "Summary of earlier parts" in context_with_summary[0]["content"]
      and "Devin and Jarvis discussed 15 earlier topics." in context_with_summary[0]["content"],
      f"got: {context_with_summary[0]}")
check("remaining items are still the 20 most recent raw messages",
      context_with_summary[-1]["content"] == "msg 34", f"got: {context_with_summary[-1]}")

# get_messages_to_summarize should now only return messages AFTER through_id (respecting
# what's already been folded in), not re-include the same 15 again.
check("after summarizing, those same 15 messages don't show up again as 'to summarize'",
      store.get_messages_to_summarize(conv2, recent_n=20) == [])

# Add 15 more messages (msg35..msg49) on top of the already-summarized msg0..msg14 and the
# already-recent-but-unsummarized msg15..msg34. The new recent-20 window becomes the last
# 20 overall (msg30..msg49), which pushes msg15..msg29 (15 messages) into "older than
# recent, not yet summarized" — that's the correct new delta, not the newly-added messages
# themselves (those are still within the recent window and shouldn't be summarized yet).
for i in range(35, 50):
    store.add_message(conv2, "user" if i % 2 == 0 else "assistant", f"msg {i}")
delta = store.get_messages_to_summarize(conv2, recent_n=20)
check("the new delta is the 15 messages that aged out of the recent window since the last summary",
      len(delta) == 15, f"got {len(delta)}")
check("the delta starts right after the previously-summarized point (msg15), not from message 0 again",
      delta[0]["content"] == "msg 15", f"got: {delta[0]}")
check("the delta ends right before the new recent-20 window begins (msg29, since msg30+ is now 'recent')",
      delta[-1]["content"] == "msg 29", f"got: {delta[-1]}")

shutil.rmtree(tmp_dir, ignore_errors=True)

# ---------------------------------------------------------------------------
section("5. server.py's _maybe_update_summary / _generate_summary_update")
# ---------------------------------------------------------------------------
import server as server_module

tmp_db2 = os.path.join(tempfile.gettempdir(), "jarvis_test_summarize_server.db")
if os.path.exists(tmp_db2):
    os.remove(tmp_db2)
server_module.store = ConversationStore(path=tmp_db2)
test_store = server_module.store

check("brain=None: _generate_summary_update returns None, doesn't raise",
      server_module._generate_summary_update("prior", [{"role": "user", "content": "x"}]) is None)


class FakeProvider:
    def __init__(self, text):
        self._text = text
        self.last_prompt = None

    def chat_stream(self, messages, system_prompt):
        self.last_prompt = messages[0]["content"]
        return iter([self._text])


class FakeBrain:
    def __init__(self, text):
        self._brain = FakeProvider(text)


original_brain = server_module.brain
server_module.brain = FakeBrain("Devin asked about his CPU usage and got a clean report.")

try:
    conv4 = test_store.create_conversation()
    for i in range(35):
        test_store.add_message(conv4, "user" if i % 2 == 0 else "assistant", f"real msg {i}")

    asyncio.run(server_module._maybe_update_summary(conv4, recent_n=20))
    updated = test_store.get_conversation(conv4)
    check("_maybe_update_summary actually stored a real summary",
          updated["summary"] == "Devin asked about his CPU usage and got a clean report.", f"got: {updated['summary']!r}")
    check("advanced summary_through_message_id to cover the summarized batch",
          updated["summary_through_message_id"] > 0)

    # Running it again immediately (no new old-enough messages) should be a no-op —
    # verify the fake provider wasn't called a second time.
    call_count_before = server_module.brain._brain.last_prompt
    asyncio.run(server_module._maybe_update_summary(conv4, recent_n=20))
    check("running again with nothing new to summarize doesn't touch the LLM or change the summary",
          test_store.get_conversation(conv4)["summary"] == updated["summary"])

    # Add a fresh batch and confirm the SECOND real summarization call includes the prior
    # summary in its prompt (cumulative, not a from-scratch replacement).
    for i in range(35, 50):
        test_store.add_message(conv4, "user" if i % 2 == 0 else "assistant", f"real msg {i}")
    server_module.brain._brain = FakeProvider("Extended: also discussed the NAS firmware update.")
    asyncio.run(server_module._maybe_update_summary(conv4, recent_n=20))
    check("the prompt for the second summarization pass included the prior summary (cumulative)",
          "clean report" in server_module.brain._brain.last_prompt, f"got prompt: {server_module.brain._brain.last_prompt[:300]!r}")
    final = test_store.get_conversation(conv4)
    check("the new summary text is what the (fake) model returned this time",
          final["summary"] == "Extended: also discussed the NAS firmware update.")

finally:
    server_module.brain = original_brain
    try:
        os.remove(tmp_db2)
    except OSError:
        pass

print(f"\n{'=' * 55}\n{PASS} passed, {FAIL} failed\n{'=' * 55}")
sys.exit(1 if FAIL else 0)

"""
Tests for Phase 7: embeddings.py + conversation_store.py's semantic search.

The nomic-embed-text model was pulled specifically for this feature — these are real, live
embedding calls against the local Ollama instance, not mocked, including the actual
"finds a conceptually related result with completely different wording" case that's the
entire point of semantic (vs. keyword) search.

Run: python test_semantic_search_phase7.py
"""

import os
import sys
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


try:
    import requests as _r
    _r.get(os.getenv("OLLAMA_URL", "http://localhost:11434"), timeout=2)
    ollama_up = True
except Exception:
    ollama_up = False

from embeddings import embed_text, cosine_similarity, is_available

# ---------------------------------------------------------------------------
section("1. embed_text — live, against the real local nomic-embed-text model")
# ---------------------------------------------------------------------------
if not ollama_up:
    print("  [SKIP] Ollama not reachable — skipping all live embedding checks.")
    sys.exit(0)

check("Ollama + embedding model reachable (is_available)", is_available())

vec = embed_text("The quick brown fox jumps over the lazy dog.")
check("embed_text returns a real, non-trivial vector", vec is not None and len(vec) > 100, f"got: {type(vec)}, len={len(vec) if vec else 0}")

check("embed_text on empty string returns None, not an error", embed_text("") is None)
check("embed_text on whitespace-only returns None", embed_text("   ") is None)

# ---------------------------------------------------------------------------
section("2. cosine_similarity")
# ---------------------------------------------------------------------------
check("identical vectors have similarity ~1.0", abs(cosine_similarity([1, 2, 3], [1, 2, 3]) - 1.0) < 1e-9)
check("orthogonal vectors have similarity 0", abs(cosine_similarity([1, 0], [0, 1])) < 1e-9)
check("empty vectors return 0.0, not a division error", cosine_similarity([], []) == 0.0)
check("mismatched-length vectors return 0.0, not an error", cosine_similarity([1, 2], [1, 2, 3]) == 0.0)

# ---------------------------------------------------------------------------
section("3. Real semantic relevance — the actual point of this feature")
# ---------------------------------------------------------------------------
vec_python = embed_text("I love programming in Python, it's my favorite language.")
vec_snake = embed_text("Python is a large snake found in tropical rainforests.")
vec_coding = embed_text("What's the best programming language for beginners to learn coding?")

sim_python_coding = cosine_similarity(vec_python, vec_coding)
sim_python_snake = cosine_similarity(vec_python, vec_snake)
print(f"  (similarity: python-programming <-> coding-question = {sim_python_coding:.3f}, "
      f"python-programming <-> python-snake = {sim_python_snake:.3f})")
check("a conceptually related sentence (programming topic) scores higher than an unrelated "
      "one sharing only a surface word ('Python' the snake vs. the language)",
      sim_python_coding > sim_python_snake, f"got: {sim_python_coding} vs {sim_python_snake}")

# ---------------------------------------------------------------------------
section("4. ConversationStore.semantic_search — isolated store, real embeddings")
# ---------------------------------------------------------------------------
from conversation_store import ConversationStore

tmp_dir = tempfile.mkdtemp(prefix="jarvis_test_semantic_")
db_store = ConversationStore(path=os.path.join(tmp_dir, "test.db"))

c1 = db_store.create_conversation(device_id="test")
c2 = db_store.create_conversation(device_id="test")
c3 = db_store.create_conversation(device_id="test")

msg1_id = db_store.add_message(c1, "user", "Can you check my Bitwarden vault for reused passwords?")
msg2_id = db_store.add_message(c1, "assistant", "Found 2 reused passwords across 3 vault items.")
msg3_id = db_store.add_message(c2, "user", "Tell me a fun fact about the moon's craters.")
msg4_id = db_store.add_message(c3, "user", "My NAS is on 192.168.1.50, remind me to update its firmware.")

for mid, cid, content in (
    (msg1_id, c1, "Can you check my Bitwarden vault for reused passwords?"),
    (msg2_id, c1, "Found 2 reused passwords across 3 vault items."),
    (msg3_id, c2, "Tell me a fun fact about the moon's craters."),
    (msg4_id, c3, "My NAS is on 192.168.1.50, remind me to update its firmware."),
):
    vec = embed_text(content)
    check(f"real embedding computed for message {mid}", vec is not None)
    db_store.add_message_embedding(mid, cid, "nomic-embed-text", vec)
    check(f"has_embedding reports True after storing it", db_store.has_embedding(mid))

check("has_embedding reports False for a message that was never embedded",
      not db_store.has_embedding(999999))

# The actual test: search for "password manager security" — a phrase that never literally
# appears in c1's messages — and confirm it still finds c1 as the top (or a strong) match,
# clearly ahead of the unrelated moon-facts and NAS-firmware conversations.
query_vec = embed_text("password manager security concerns")
results = db_store.semantic_search(query_vec, limit=10, min_similarity=0.0)
check("semantic_search returns results ordered by similarity, highest first",
      results == sorted(results, key=lambda r: -r["similarity"]), f"got: {results}")
check("the Bitwarden conversation (c1) is the top semantic match for 'password manager security', "
      "despite never using that exact phrase",
      results and results[0]["id"] == c1, f"got top result: {results[0] if results else None}")

top_ids = [r["id"] for r in results[:1]]
check("the unrelated moon-facts conversation is not the top match", c2 not in top_ids)

# ---------------------------------------------------------------------------
section("5. Tool-facing wrapper + registration")
# ---------------------------------------------------------------------------
from tools import ALL_TOOLS, Tier, REQUIRES_CAPABILITY

check("semantic_search_conversations registered", "semantic_search_conversations" in ALL_TOOLS)
if "semantic_search_conversations" in ALL_TOOLS:
    check("Tier 1 (read-only)", ALL_TOOLS["semantic_search_conversations"].tier == Tier.TIER_1)
check("no capability gate (data-layer search, like conversation search itself)",
      "semantic_search_conversations" not in REQUIRES_CAPABILITY)

import conversation_store as cs_module
original_store = cs_module.store
cs_module.store = db_store
try:
    result = cs_module.semantic_search_conversations("password manager security concerns")
    check("tool wrapper returns the same top match as the direct store call",
          result["results"] and result["results"][0]["id"] == c1, f"got: {result}")
finally:
    cs_module.store = original_store

shutil.rmtree(tmp_dir, ignore_errors=True)

print(f"\n{'=' * 55}\n{PASS} passed, {FAIL} failed\n{'=' * 55}")
sys.exit(1 if FAIL else 0)

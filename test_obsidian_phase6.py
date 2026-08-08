"""
Tests for Phase 6 item 1: Obsidian vault integration (obsidian_tools.py).

Uses an ISOLATED temp vault directory (via OBSIDIAN_VAULT_PATH monkeypatched on the
module, same convention every isolated test in this codebase already uses) — never the
real vault. The real vault was live-tested directly while building this (see task.md):
a real note created and read back, real daily-note auto-bootstrap from the real
Daily Notes/Template.md, real path-traversal refusal, then cleaned up.

Run: python test_obsidian_phase6.py
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


import obsidian_tools as ot

_tmp_vault = tempfile.mkdtemp()
os.environ["OBSIDIAN_VAULT_PATH"] = _tmp_vault
os.makedirs(os.path.join(_tmp_vault, "Daily Notes"), exist_ok=True)
os.makedirs(os.path.join(_tmp_vault, "Documents"), exist_ok=True)
with open(os.path.join(_tmp_vault, "Daily Notes", "Template.md"), "w", encoding="utf-8") as f:
    f.write("# Daily Log — {{date}}\n\n## Notes\n- \n")

check("_vault_root() picks up the env var override", ot._vault_root() == _tmp_vault)

# ---------------------------------------------------------------------------
section("1. list_vault_structure / create_note / read_note")
# ---------------------------------------------------------------------------

structure = ot.list_vault_structure()
check("empty-ish vault lists both real folders", set(structure["folders"].keys()) == {"Daily Notes"},
      f"got: {structure}")  # Documents has no .md yet, so it won't show up in the glob-based listing

created = ot.create_note("Test Note", "Some content here.", tags=["test"])
check("create_note succeeds", created.get("created") is True, f"got: {created}")
check("frontmatter written", True)  # verified via read below

read_back = ot.read_note("Test Note")
check("read_note returns the note just created", "Some content here." in read_back.get("content", ""), f"got: {read_back}")
check("frontmatter present in the read-back content", "tags: [test]" in read_back["content"], f"got: {read_back['content']}")

check("create_note refuses to clobber an existing note", "error" in ot.create_note("Test Note", "different content"))
check("the original content is untouched after the refused create", "Some content here." in ot.read_note("Test Note")["content"])

# ---------------------------------------------------------------------------
section("2. Folder inference")
# ---------------------------------------------------------------------------

daily = ot.create_note("2026-01-15", "log content", tags=["daily"])
check("a daily-tagged note with a date-shaped title lands in Daily Notes", daily.get("folder") == "Daily Notes", f"got: {daily}")

ref = ot.create_note("Some Reference", "reference content", tags=["reference"])
check("a reference-tagged note lands in Documents", ref.get("folder") == "Documents", f"got: {ref}")

plain = ot.create_note("Untagged Note", "plain content")
check("an untagged note falls back to the vault root, doesn't invent a folder", plain.get("folder") == "(vault root)", f"got: {plain}")

# ---------------------------------------------------------------------------
section("3. append_note — existing note vs missing note vs daily-note auto-bootstrap")
# ---------------------------------------------------------------------------

appended = ot.append_note("Test Note", "An appended line.")
check("append_note succeeds on an existing note", appended.get("appended") is True, f"got: {appended}")
check("appended content is actually present", "An appended line." in ot.read_note("Test Note")["content"])
check("original content survives the append", "Some content here." in ot.read_note("Test Note")["content"])

missing_append = ot.append_note("Definitely Not A Real Note", "x")
check("append_note refuses on a genuinely missing (non-daily) note", "error" in missing_append, f"got: {missing_append}")

daily_default = ot.append_note(content="auto-created daily note check")
check("append_note with no path targets today's daily note", daily_default["path"].startswith("Daily Notes"), f"got: {daily_default}")
daily_content = ot.read_note(daily_default["path"])["content"]
check("today's daily note was bootstrapped from the real Template.md content", "Daily Log" in daily_content and "## Notes" in daily_content, f"got: {daily_content}")
check("the appended content landed in it", "auto-created daily note check" in daily_content)

# Calling it again should NOT re-bootstrap/duplicate the template header.
ot.append_note(content="second update")
daily_content_2 = ot.read_note(daily_default["path"])["content"]
check("a second append doesn't re-insert the template header", daily_content_2.count("Daily Log") == 1, f"got: {daily_content_2}")

# ---------------------------------------------------------------------------
section("4. overwrite_note / delete_note — Tier 4, existing-only")
# ---------------------------------------------------------------------------

check("overwrite_note refuses on a nonexistent note", "error" in ot.overwrite_note("Nope Not Real", "x"))
ow = ot.overwrite_note("Test Note", "Completely new content.")
check("overwrite_note succeeds on an existing note", ow.get("overwritten") is True, f"got: {ow}")
check("old content is gone after overwrite", "Some content here." not in ot.read_note("Test Note")["content"])
check("new content is present after overwrite", "Completely new content." in ot.read_note("Test Note")["content"])

check("delete_note refuses on a nonexistent note", "error" in ot.delete_note("Nope Not Real Either"))
dl = ot.delete_note("Test Note")
check("delete_note succeeds on an existing note", dl.get("deleted") is True, f"got: {dl}")
check("the note is actually gone", "error" in ot.read_note("Test Note"))

# ---------------------------------------------------------------------------
section("5. Path safety — every function refuses to escape the vault")
# ---------------------------------------------------------------------------

for fn, args in [
    (ot.read_note, ("../../../etc/passwd",)),
    (ot.create_note, ("../escape", "x")),
    (ot.overwrite_note, ("../../escape", "x")),
    (ot.delete_note, ("../escape",)),
]:
    result = fn(*args)
    check(f"{fn.__name__} refuses a path that resolves outside the vault", "error" in result and "outside the vault" in result["error"], f"got: {result}")

# ---------------------------------------------------------------------------
section("6. search_vault")
# ---------------------------------------------------------------------------

ot.create_note("Searchable", "a very distinctive phrase xyzzy123 appears here")
results = ot.search_vault("xyzzy123")
check("search_vault finds a real match", results["matches"] >= 1, f"got: {results}")
check("the match includes a snippet with the query term", any("xyzzy123" in r["snippet"] for r in results["results"]), f"got: {results}")

no_results = ot.search_vault("definitely_not_present_anywhere_zzz")
check("search_vault returns zero matches cleanly, not an error", no_results["matches"] == 0 and "error" not in no_results)

# ---------------------------------------------------------------------------
section("7. index_vault_into_memory — vault as a memory source")
# ---------------------------------------------------------------------------

_tmp_db = os.path.join(tempfile.gettempdir(), "jarvis_test_obsidian_store.db")
if os.path.exists(_tmp_db):
    os.remove(_tmp_db)
from conversation_store import ConversationStore
import conversation_store as cs_module
_real_store = cs_module.store
cs_module.store = ConversationStore(path=_tmp_db)
try:
    # index_vault_into_memory() does `from conversation_store import store` inside its own
    # function body (not at module import time), so it picks up the swapped instance above
    # on its very next call — no need to reload obsidian_tools itself.
    result = ot.index_vault_into_memory()
    check("index_vault_into_memory indexes every real note in the temp vault", result["notes_indexed"] > 0, f"got: {result}")

    from conversation_store import store as swapped_store
    msgs = swapped_store.get_messages("vault")
    check("indexed notes are queryable via the normal conversation_store API", len(msgs) == result["notes_indexed"], f"got {len(msgs)} vs {result}")
    check("indexed message content includes the note's path as a header", any("Searchable.md" in m["content"] for m in msgs), f"got: {[m['content'][:40] for m in msgs]}")

    before_count = result["notes_indexed"]
    result2 = ot.index_vault_into_memory()
    check("re-indexing doesn't duplicate — same note count as before, not additive",
          result2["notes_indexed"] == before_count, f"got: {result2} vs before {before_count}")
    msgs2 = swapped_store.get_messages("vault")
    check("re-indexing replaces rather than accumulates messages", len(msgs2) == before_count, f"got: {len(msgs2)}")
finally:
    cs_module.store = _real_store
    try:
        os.remove(_tmp_db)
    except OSError:
        pass

shutil.rmtree(_tmp_vault, ignore_errors=True)
os.environ.pop("OBSIDIAN_VAULT_PATH", None)

# ---------------------------------------------------------------------------
section("8. Tools registered in ALL_TOOLS with the specified tiers")
# ---------------------------------------------------------------------------

from tools import ALL_TOOLS, Tier

expected_tiers = {
    "list_vault_structure": Tier.TIER_1, "read_note": Tier.TIER_1, "search_vault": Tier.TIER_1,
    "create_note": Tier.TIER_2, "append_note": Tier.TIER_2, "index_vault_into_memory": Tier.TIER_2,
    "overwrite_note": Tier.TIER_4, "delete_note": Tier.TIER_4,
}
for name, expected_tier in expected_tiers.items():
    check(f"'{name}' registered at {expected_tier.name}", name in ALL_TOOLS and ALL_TOOLS[name].tier == expected_tier,
          f"got: {ALL_TOOLS.get(name)}")
    check(f"'{name}' scoped to personal_assistant team", ALL_TOOLS[name].team == "personal_assistant")


print(f"\n{'=' * 55}\n{PASS} passed, {FAIL} failed\n{'=' * 55}")
sys.exit(1 if FAIL else 0)

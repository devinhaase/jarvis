"""
Tests for Phase 7: file_tools.py — scoped read/list/write/move for local files.

Runs entirely inside an isolated temp directory — never touches real project files.

Run: python test_file_tools_phase7.py
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


from tools import ALL_TOOLS, Tier, REQUIRES_CAPABILITY

# ---------------------------------------------------------------------------
section("1. Tool registration, tiers, capability")
# ---------------------------------------------------------------------------
expected_tiers = {
    "read_local_file": Tier.TIER_1, "list_directory": Tier.TIER_1,
    "write_local_file": Tier.TIER_2, "move_or_rename_path": Tier.TIER_3,
}
for name, tier in expected_tiers.items():
    check(f"{name} registered", name in ALL_TOOLS)
    if name in ALL_TOOLS:
        check(f"{name} is {tier.name}", ALL_TOOLS[name].tier == tier, f"got: {ALL_TOOLS[name].tier}")
    check(f"{name} requires 'filesystem' capability", REQUIRES_CAPABILITY.get(name) == "filesystem")

# ---------------------------------------------------------------------------
section("2. read_local_file")
# ---------------------------------------------------------------------------
from file_tools import read_local_file, list_directory, write_local_file, move_or_rename_path

tmp_dir = tempfile.mkdtemp(prefix="jarvis_test_files_")
sample_path = os.path.join(tmp_dir, "sample.txt")
with open(sample_path, 'w') as f:
    f.write("Hello, Jarvis.\nLine two.\n")

result = read_local_file(sample_path)
check("reads real content", result.get("content") == "Hello, Jarvis.\nLine two.\n", f"got: {result}")
check("not truncated for a small file", result.get("truncated") is False)

result_missing = read_local_file(os.path.join(tmp_dir, "nope.txt"))
check("missing file returns a clean error, doesn't raise", "error" in result_missing)

result_dir = read_local_file(tmp_dir)
check("reading a directory as a file returns a clean error", "error" in result_dir)

big_path = os.path.join(tmp_dir, "big.txt")
with open(big_path, 'w') as f:
    f.write("x" * 100)
result_truncated = read_local_file(big_path, max_chars=10)
check("respects max_chars", len(result_truncated["content"]) == 10, f"got: {len(result_truncated['content'])}")
check("reports truncated=True when content exceeds max_chars", result_truncated["truncated"] is True)

# ---------------------------------------------------------------------------
section("3. list_directory")
# ---------------------------------------------------------------------------
os.makedirs(os.path.join(tmp_dir, "subdir"), exist_ok=True)
result = list_directory(tmp_dir)
names = {e["name"] for e in result["entries"]}
check("lists files and the subdirectory", {"sample.txt", "big.txt", "subdir"} <= names, f"got: {names}")
subdir_entry = next(e for e in result["entries"] if e["name"] == "subdir")
check("directory entries are flagged is_dir=True with no size", subdir_entry["is_dir"] is True and subdir_entry["size_bytes"] is None)
file_entry = next(e for e in result["entries"] if e["name"] == "sample.txt")
check("file entries have a real size_bytes", file_entry["is_dir"] is False and file_entry["size_bytes"] > 0)

result_missing_dir = list_directory(os.path.join(tmp_dir, "nope"))
check("missing directory returns a clean error", "error" in result_missing_dir)

result_not_dir = list_directory(sample_path)
check("listing a file (not a directory) returns a clean error", "error" in result_not_dir)

# ---------------------------------------------------------------------------
section("4. write_local_file")
# ---------------------------------------------------------------------------
write_path = os.path.join(tmp_dir, "new_notes", "output.txt")
result = write_local_file(write_path, "First line.\n")
check("write creates parent directories that don't exist yet", os.path.exists(write_path))
check("overwrite mode wrote the expected content", open(write_path).read() == "First line.\n")

result2 = write_local_file(write_path, "Second write, overwrite mode.\n", mode="overwrite")
check("overwrite mode replaces prior content entirely", open(write_path).read() == "Second write, overwrite mode.\n")

result3 = write_local_file(write_path, "Appended line.\n", mode="append")
check("append mode adds to existing content rather than replacing it",
      open(write_path).read() == "Second write, overwrite mode.\nAppended line.\n")

result_bad_mode = write_local_file(write_path, "x", mode="not-a-real-mode")
check("an invalid mode is rejected with a clear error", "error" in result_bad_mode)

# ---------------------------------------------------------------------------
section("5. move_or_rename_path")
# ---------------------------------------------------------------------------
src = os.path.join(tmp_dir, "move_me.txt")
with open(src, 'w') as f:
    f.write("movable content")
dst = os.path.join(tmp_dir, "moved_subdir", "renamed.txt")

result = move_or_rename_path(src, dst)
check("move succeeds and reports moved=True", result.get("moved") is True, f"got: {result}")
check("source no longer exists after the move", not os.path.exists(src))
check("destination now has the content", os.path.exists(dst) and open(dst).read() == "movable content")

result_missing_src = move_or_rename_path(os.path.join(tmp_dir, "not_real.txt"), os.path.join(tmp_dir, "wherever.txt"))
check("moving a nonexistent source returns a clean error", "error" in result_missing_src)

# Refuses to clobber an existing destination
existing_dst = os.path.join(tmp_dir, "already_here.txt")
with open(existing_dst, 'w') as f:
    f.write("original content — must survive")
another_src = os.path.join(tmp_dir, "another.txt")
with open(another_src, 'w') as f:
    f.write("should not overwrite anything")

result_clobber = move_or_rename_path(another_src, existing_dst)
check("refuses to overwrite an existing destination", "error" in result_clobber, f"got: {result_clobber}")
check("the original destination content is untouched", open(existing_dst).read() == "original content — must survive")
check("the source that would have been moved is still in place (move was refused, not partial)",
      os.path.exists(another_src))

shutil.rmtree(tmp_dir, ignore_errors=True)

print(f"\n{'=' * 55}\n{PASS} passed, {FAIL} failed\n{'=' * 55}")
sys.exit(1 if FAIL else 0)

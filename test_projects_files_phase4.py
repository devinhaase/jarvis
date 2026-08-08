"""
Tests for Phase 4a: Projects (chat folders) and file attachments in conversation_store.py.

Uses an isolated temp DB throughout — never touches the real data/jarvis.db.

Run: python test_projects_files_phase4.py
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


from conversation_store import ConversationStore

TMP_DIR = tempfile.mkdtemp(prefix="jarvis_test_projects_")
DB_PATH = os.path.join(TMP_DIR, "test.db")
store = ConversationStore(path=DB_PATH)

# ---------------------------------------------------------------------------
section("1. Project CRUD")
# ---------------------------------------------------------------------------
p1 = store.create_project("Website Redesign")
p2 = store.create_project("  ")  # blank name falls back to a default
check("create_project returns an id", bool(p1))
check("blank project name falls back to a default", store.get_project(p2)["name"] == "New project")
check("project_exists true for a real id", store.project_exists(p1))
check("project_exists false for a random id", not store.project_exists("nope"))

projects = store.list_projects()
check("list_projects includes both, each with conversation_count=0",
      all(p["conversation_count"] == 0 for p in projects if p["id"] in (p1, p2)))

store.rename_project(p1, "Website Redesign 2.0")
check("rename_project persists", store.get_project(p1)["name"] == "Website Redesign 2.0")

# ---------------------------------------------------------------------------
section("2. Assigning conversations to a project")
# ---------------------------------------------------------------------------
c1 = store.create_conversation(device_id="desktop")
c2 = store.create_conversation(device_id="desktop")
c3 = store.create_conversation(device_id="desktop")  # stays unassigned

check("a fresh conversation has no project_id", store.get_conversation(c1)["project_id"] is None)

store.set_conversation_project(c1, p1)
store.set_conversation_project(c2, p1)
check("set_conversation_project persists", store.get_conversation(c1)["project_id"] == p1)

in_project = store.list_conversations(project_id=p1)
check("list_conversations(project_id=..) returns exactly the assigned conversations",
      {c["id"] for c in in_project} == {c1, c2}, f"got: {[c['id'] for c in in_project]}")

all_convs = store.list_conversations()
check("list_conversations() with no filter still returns everything, including unassigned c3",
      c3 in {c["id"] for c in all_convs})

projects_after = {p["id"]: p for p in store.list_projects()}
check("project's conversation_count reflects the two assigned conversations",
      projects_after[p1]["conversation_count"] == 2, f"got: {projects_after[p1]}")

store.set_conversation_project(c1, None)
check("set_conversation_project(None) unassigns", store.get_conversation(c1)["project_id"] is None)
store.set_conversation_project(c1, p1)  # reassign for later sections

# ---------------------------------------------------------------------------
section("3. Deleting a project unassigns conversations, doesn't delete them")
# ---------------------------------------------------------------------------
p_temp = store.create_project("Temp Project")
c_temp = store.create_conversation()
store.set_conversation_project(c_temp, p_temp)
store.add_file("note.txt", "some project-scoped content", 10, project_id=p_temp)

store.delete_project(p_temp)
check("project itself is gone", not store.project_exists(p_temp))
check("its conversation survives, just unassigned",
      store.conversation_exists(c_temp) and store.get_conversation(c_temp)["project_id"] is None)
check("the project's files are deleted along with it", store.list_files(project_id=p_temp) == [])

# ---------------------------------------------------------------------------
section("4. File attachments — conversation-scoped vs project-scoped visibility")
# ---------------------------------------------------------------------------
# c1, c2 are both in project p1. c3 is unassigned.
f_conv_only = store.add_file("private_notes.txt", "just for c3", 20, conversation_id=c3)
f_project = store.add_file("shared_spec.md", "shared across the whole project", 40, project_id=p1)
f_conv_in_project = store.add_file("c1_specific.txt", "attached directly to c1", 15, conversation_id=c1)

c3_context = store.get_context_files(c3)
check("c3 (no project) sees only its own directly-attached file",
      {f["id"] for f in c3_context} == {f_conv_only}, f"got: {[f['filename'] for f in c3_context]}")

c1_context = store.get_context_files(c1)
check("c1 (in project p1) sees both the project file AND its own direct attachment",
      {f["id"] for f in c1_context} == {f_project, f_conv_in_project},
      f"got: {[f['filename'] for f in c1_context]}")

c2_context = store.get_context_files(c2)
check("c2 (also in project p1, but nothing attached directly to it) still sees the shared project file",
      {f["id"] for f in c2_context} == {f_project}, f"got: {[f['filename'] for f in c2_context]}")

check("c2 does NOT see c1's private direct attachment", f_conv_in_project not in {f["id"] for f in c2_context})
check("c2 does NOT see c3's file at all", f_conv_only not in {f["id"] for f in c2_context})

# get_context_files returns full rows (content_text); list_files is the lighter UI-facing view
check("get_context_files includes content_text",
      any(f["id"] == f_project and f["content_text"] == "shared across the whole project" for f in c1_context))

conv_files = store.list_files(conversation_id=c1)
check("list_files(conversation_id=..) returns only directly-attached files, not project-wide ones",
      {f["id"] for f in conv_files} == {f_conv_in_project}, f"got: {[f['filename'] for f in conv_files]}")

project_files = store.list_files(project_id=p1)
check("list_files(project_id=..) returns only project-scoped files",
      {f["id"] for f in project_files} == {f_project}, f"got: {[f['filename'] for f in project_files]}")

# ---------------------------------------------------------------------------
section("5. Delete file / delete conversation cascades")
# ---------------------------------------------------------------------------
store.delete_file(f_conv_only)
check("deleted file is gone", store.get_file(f_conv_only) is None)
check("c3's context is now empty", store.get_context_files(c3) == [])

before_delete = store.list_files(conversation_id=c1)
check("sanity: c1 has a direct attachment before delete", len(before_delete) == 1)
store.delete_conversation(c1)
check("deleting a conversation cascades to delete its directly-attached files",
      store.list_files(conversation_id=c1) == [])
check("but the project-scoped file survives (it wasn't c1's to delete)",
      store.get_file(f_project) is not None)

# ---------------------------------------------------------------------------
shutil.rmtree(TMP_DIR, ignore_errors=True)

print(f"\n{'=' * 55}\n{PASS} passed, {FAIL} failed\n{'=' * 55}")
sys.exit(1 if FAIL else 0)

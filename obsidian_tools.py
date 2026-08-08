"""
obsidian_tools.py — Phase 6: read/write access to Devin's real Obsidian vault
(OBSIDIAN_VAULT_PATH, defaults to the real path found on this machine —
C:\\Users\\devin\\OneDrive\\Documents\\Obsidian Vault). Personal-Assistant-team-scoped: a
vault is personal knowledge, not IT/security state.

Vault reality check (done before writing this, not assumed): the real vault currently has
no YAML frontmatter, no tags, and no [[wikilinks]] in any of its 6 existing notes — it's
two folders (`Daily Notes/`, with a `Template.md` daily-log format, and `Documents/`, holding
reference material) written in plain markdown. So "respect existing conventions" here means
two things at once: (1) don't restructure what's already there, fit new notes into the same
two-folder shape, and (2) still write clean frontmatter/tags on *new* notes going forward —
that's a real, useful Obsidian convention the vault just hasn't needed yet at 6 notes, not
something to avoid because it's unprecedented.

Tiers, exactly as specified: create/append = Tier 2 (autonomous + logged). Overwrite/delete
of an EXISTING note = Tier 4 (never touch an existing note without asking, no exceptions).
Read/search = Tier 1.

Every path is resolved and confined to the vault root — this tool's whole purpose is a
bounded, trusted domain (unlike file_tools.py's genuinely general-purpose, unconfined file
access), so a path like "../../../Windows/System32/evil.md" is refused outright rather than
just being unlikely to come up.
"""

import os
import re
import glob as _glob
from datetime import datetime, timezone

DEFAULT_VAULT_PATH = r"C:\Users\devin\OneDrive\Documents\Obsidian Vault"


def _vault_root() -> str:
    return os.getenv("OBSIDIAN_VAULT_PATH", DEFAULT_VAULT_PATH)


def _resolve(relative_path: str) -> str:
    """Joins `relative_path` onto the vault root and refuses anything that would resolve
    outside it — the one thing every function below depends on for safety."""
    root = os.path.abspath(_vault_root())
    candidate = os.path.abspath(os.path.join(root, relative_path))
    if os.path.commonpath([root, candidate]) != root:
        raise ValueError(f"'{relative_path}' resolves outside the vault — refused.")
    return candidate


def _ensure_md(path: str) -> str:
    return path if path.endswith(".md") else path + ".md"


def _frontmatter(title: str, tags: list = None) -> str:
    tags = tags or []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tag_line = "[" + ", ".join(tags) + "]" if tags else "[]"
    return (
        "---\n"
        f"title: {title}\n"
        f"created: {now}\n"
        f"tags: {tag_line}\n"
        "source: jarvis\n"
        "---\n\n"
    )


def _infer_folder(title: str, tags: list, content: str) -> str:
    """Best-effort placement into the vault's existing two-folder shape, not a hardcoded
    guess — actually lists what folders exist right now so a vault that grows a third
    top-level folder later doesn't need this function edited. Falls back to vault root
    (no subfolder) if nothing matches, rather than inventing a new folder unasked."""
    root = _vault_root()
    if not os.path.isdir(root):
        return ""
    existing = {name for name in os.listdir(root) if os.path.isdir(os.path.join(root, name)) and not name.startswith(".")}

    lowered_tags = {t.lower() for t in (tags or [])}
    daily_re = re.compile(r'^\d{4}-\d{2}-\d{2}$')
    if "daily notes" in {e.lower() for e in existing} and (daily_re.match(title) or "daily" in lowered_tags):
        return next(e for e in existing if e.lower() == "daily notes")
    if "documents" in {e.lower() for e in existing} and (lowered_tags & {"reference", "study", "notes"}):
        return next(e for e in existing if e.lower() == "documents")
    return ""


def list_vault_structure() -> dict:
    """Tier 1 — the folder tree + note count per folder, so a caller (human or the model
    deciding where a new note belongs) can see the vault's real shape rather than guessing."""
    root = _vault_root()
    if not os.path.isdir(root):
        return {"error": f"Vault not found at '{root}'. Set OBSIDIAN_VAULT_PATH if it's elsewhere."}

    folders = {}
    for path in _glob.glob(os.path.join(root, "**", "*.md"), recursive=True):
        rel = os.path.relpath(path, root)
        folder = os.path.dirname(rel) or "(vault root)"
        folders.setdefault(folder, []).append(os.path.basename(rel))

    return {"vault_path": root, "folders": {k: sorted(v) for k, v in sorted(folders.items())}}


def read_note(path: str) -> dict:
    """Tier 1."""
    try:
        full = _resolve(_ensure_md(path))
    except ValueError as e:
        return {"path": path, "error": str(e)}
    if not os.path.exists(full):
        return {"path": path, "error": "Note not found."}
    with open(full, encoding="utf-8", errors="replace") as f:
        return {"path": path, "content": f.read()}


def search_vault(query: str, limit: int = 10) -> dict:
    """Tier 1 — plain keyword search across every note (title + body), case-insensitive.
    Complements the semantic vault indexing (see index_vault_into_memory below) rather than
    replacing it: this finds exact/near matches fast with no embedding dependency; semantic
    search finds conceptually related notes even with zero literal word overlap."""
    root = _vault_root()
    if not os.path.isdir(root):
        return {"error": f"Vault not found at '{root}'."}

    query_lower = query.lower()
    results = []
    for path in _glob.glob(os.path.join(root, "**", "*.md"), recursive=True):
        rel = os.path.relpath(path, root)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception:
            continue
        if query_lower in content.lower() or query_lower in rel.lower():
            idx = content.lower().find(query_lower)
            snippet = content[max(0, idx - 60):idx + 120].strip() if idx >= 0 else content[:150].strip()
            results.append({"path": rel, "snippet": snippet})
        if len(results) >= limit:
            break

    return {"query": query, "matches": len(results), "results": results}


def create_note(title: str, content: str, folder: str = None, tags: list = None, links: list = None) -> dict:
    """Tier 2 — autonomous + logged. Refuses if the target already exists (that's an
    overwrite, a different tier and a different function) rather than silently clobbering
    an existing note. Writes real frontmatter even though this vault's existing notes
    don't have any yet — a sensible default for new notes, not a rewrite of old ones."""
    folder = folder if folder is not None else _infer_folder(title, tags or [], content)
    rel_path = os.path.join(folder, _ensure_md(title)) if folder else _ensure_md(title)

    try:
        full = _resolve(rel_path)
    except ValueError as e:
        return {"path": rel_path, "error": str(e)}
    if os.path.exists(full):
        return {"path": rel_path, "error": "A note already exists at this path — use append_note "
                                            "to add to it or overwrite_note (Tier 4) to replace it."}

    body = _frontmatter(title, tags) + content.strip() + "\n"
    if links:
        body += "\n## Related\n" + "\n".join(f"- [[{link}]]" for link in links) + "\n"

    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(body)
    return {"path": rel_path, "created": True, "folder": folder or "(vault root)", "bytes_written": len(body.encode("utf-8"))}


def _todays_daily_note_path() -> str:
    root = _vault_root()
    daily_folder = next((n for n in os.listdir(root) if n.lower() == "daily notes"), "Daily Notes") if os.path.isdir(root) else "Daily Notes"
    today = datetime.now().strftime("%Y-%m-%d")
    return os.path.join(daily_folder, f"{today}.md")


def append_note(path: str = None, content: str = "") -> dict:
    """Tier 2. `path=None` means "today's daily note" — auto-bootstraps it from
    Daily Notes/Template.md if today's note doesn't exist yet (the normal Obsidian
    daily-notes workflow), since that's a deliberate, expected creation, not an
    accidental one. Any OTHER missing path is refused rather than silently created —
    append implies the target already exists; use create_note for a genuinely new note."""
    is_daily_default = path is None
    rel_path = path or _todays_daily_note_path()

    try:
        full = _resolve(_ensure_md(rel_path))
    except ValueError as e:
        return {"path": rel_path, "error": str(e)}

    if not os.path.exists(full):
        if not is_daily_default:
            return {"path": rel_path, "error": "Note not found — append_note only adds to an "
                                                "existing note (except today's daily note, which "
                                                "auto-creates from the template). Use create_note "
                                                "for a genuinely new note."}
        template_path = os.path.join(_vault_root(), os.path.dirname(rel_path), "Template.md")
        today = datetime.now().strftime("%Y-%m-%d")
        if os.path.exists(template_path):
            # errors="replace": a template file saved by some other editor/tool in a
            # non-UTF-8 codepage shouldn't crash the whole append — same defensive-read
            # convention file_tools.py's read_local_file already uses.
            with open(template_path, encoding="utf-8", errors="replace") as f:
                base = f.read().replace("{{date}}", today)
        else:
            base = f"# Daily Log — {today}\n\n"
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(base)

    timestamp = datetime.now().strftime("%H:%M")
    addition = f"\n## Update — {timestamp}\n{content.strip()}\n"
    with open(full, "a", encoding="utf-8") as f:
        f.write(addition)
    return {"path": rel_path, "appended": True, "bytes_written": len(addition.encode("utf-8"))}


def overwrite_note(path: str, content: str) -> dict:
    """Tier 4 — confirmation required every time. Only operates on a note that already
    exists (an overwrite of nothing is just create_note, wrong function for that)."""
    try:
        full = _resolve(_ensure_md(path))
    except ValueError as e:
        return {"path": path, "error": str(e)}
    if not os.path.exists(full):
        return {"path": path, "error": "Note not found — nothing to overwrite. Use create_note for a new note."}
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    return {"path": path, "overwritten": True, "bytes_written": len(content.encode("utf-8"))}


def delete_note(path: str) -> dict:
    """Tier 4 — confirmation required every time."""
    try:
        full = _resolve(_ensure_md(path))
    except ValueError as e:
        return {"path": path, "error": str(e)}
    if not os.path.exists(full):
        return {"path": path, "error": "Note not found — nothing to delete."}
    os.remove(full)
    return {"path": path, "deleted": True}


# ---------------------------------------------------------------------------
# Vault as a memory source (not just an output target) — indexes note content into the
# same embedding infrastructure Phase 7 built for conversation semantic search, so vault
# knowledge can surface the same way. Reuses embeddings.py/conversation_store.py rather
# than building a second embedding store.
# ---------------------------------------------------------------------------

def index_vault_into_memory() -> dict:
    """Tier 1 (read-only from the vault's perspective — it writes to Jarvis's own semantic
    search index, not to the vault). Embeds each note's content and stores it tagged with a
    synthetic conversation_id ('vault') so vault notes are searchable via the exact same
    semantic_search_conversations tool conversations already use — one search surface, not
    two competing ones. Degrades gracefully (skips embedding, still returns note count) if
    the local embedding model isn't available, same convention as every other embedding-
    dependent feature in this codebase."""
    root = _vault_root()
    if not os.path.isdir(root):
        return {"error": f"Vault not found at '{root}'."}

    from conversation_store import store
    try:
        from embeddings import embed_text, EMBEDDING_MODEL
        embeddings_available = True
    except Exception:
        embeddings_available = False

    VAULT_CONVERSATION_ID = "vault"
    if not store.conversation_exists(VAULT_CONVERSATION_ID):
        store._conn.execute(
            "INSERT OR IGNORE INTO conversations (id, title, created_at, updated_at) VALUES (?,?,?,?)",
            (VAULT_CONVERSATION_ID, "Obsidian Vault", 0, 0),
        )
        store._conn.commit()
    else:
        # Re-indexing (a note changed, a new one appeared) always starts from a clean
        # slate for this synthetic conversation, rather than appending duplicates of notes
        # indexed on a previous run — message_embeddings rows cascade via message_id.
        with store._lock:
            ids = [r[0] for r in store._conn.execute(
                "SELECT id FROM messages WHERE conversation_id=?", (VAULT_CONVERSATION_ID,)
            ).fetchall()]
            if ids:
                placeholders = ",".join("?" * len(ids))
                store._conn.execute(f"DELETE FROM message_embeddings WHERE message_id IN ({placeholders})", ids)
                store._conn.execute("DELETE FROM messages WHERE conversation_id=?", (VAULT_CONVERSATION_ID,))
                store._conn.commit()

    indexed, embedded = 0, 0
    for path in _glob.glob(os.path.join(root, "**", "*.md"), recursive=True):
        rel = os.path.relpath(path, root)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception:
            continue
        if not content.strip():
            continue
        msg_id = store.add_message(VAULT_CONVERSATION_ID, "assistant", f"[{rel}]\n{content}", source="vault")
        indexed += 1
        if embeddings_available:
            vector = embed_text(content)
            if vector:
                store.add_message_embedding(msg_id, VAULT_CONVERSATION_ID, EMBEDDING_MODEL, vector)
                embedded += 1

    return {"notes_indexed": indexed, "notes_embedded": embedded, "embeddings_available": embeddings_available}

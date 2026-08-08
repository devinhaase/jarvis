"""
file_tools.py — Scoped file read/write/organize, so common file requests don't have to go
through run_local_script's arbitrary-shell-command hammer (Tier 3, unstructured output).
These are Tier-appropriate structured alternatives: read/list are Tier 1, writing is
Tier 2 (reversible/logged, same tier draft_local_note already uses), moving/renaming is
Tier 3 (notify-then-act — relocating an existing file is more consequential than creating
a new one). None of this widens what's already possible — run_local_script already allows
arbitrary shell commands — it just gives the LLM a safer, structured way to do the common
cases instead of shelling out for everything.
"""

import os
import shutil


def read_local_file(path: str, max_chars: int = 5000) -> dict:
    """Read a local text file's content, truncated to max_chars."""
    if not os.path.exists(path):
        return {"path": path, "error": "File not found"}
    if os.path.isdir(path):
        return {"path": path, "error": "Path is a directory, not a file — use list_directory instead"}
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read(max_chars + 1)
        truncated = len(content) > max_chars
        return {
            "path": path, "content": content[:max_chars], "truncated": truncated,
            "size_bytes": os.path.getsize(path),
        }
    except Exception as e:
        return {"path": path, "error": str(e)}


def list_directory(path: str = ".") -> dict:
    """List a directory's immediate contents (not recursive) — name, is_dir, size_bytes."""
    if not os.path.exists(path):
        return {"path": path, "error": "Path not found"}
    if not os.path.isdir(path):
        return {"path": path, "error": "Path is not a directory"}
    try:
        entries = []
        for name in sorted(os.listdir(path)):
            full = os.path.join(path, name)
            is_dir = os.path.isdir(full)
            entries.append({
                "name": name, "is_dir": is_dir,
                "size_bytes": None if is_dir else os.path.getsize(full),
            })
        return {"path": path, "entries": entries, "count": len(entries)}
    except Exception as e:
        return {"path": path, "error": str(e)}


def write_local_file(path: str, content: str, mode: str = "overwrite") -> dict:
    """Write (or append to) a local text file. mode='overwrite' replaces existing content
    entirely — there's no undo for that beyond a backup, same as any other file write."""
    if mode not in ("overwrite", "append"):
        return {"path": path, "error": "mode must be 'overwrite' or 'append'"}
    file_mode = 'w' if mode == "overwrite" else 'a'
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, file_mode, encoding='utf-8') as f:
            f.write(content)
        return {"path": path, "mode": mode, "bytes_written": len(content.encode('utf-8'))}
    except Exception as e:
        return {"path": path, "error": str(e)}


def move_or_rename_path(src: str, dst: str) -> dict:
    """Move or rename a file/directory. Refuses if dst already exists rather than silently
    overwriting it — delete or rename the destination first if that's really the intent."""
    if not os.path.exists(src):
        return {"src": src, "dst": dst, "error": f"Source path not found: {src}"}
    if os.path.exists(dst):
        return {"src": src, "dst": dst,
                "error": f"Destination already exists: {dst} — refusing to overwrite it."}
    try:
        parent = os.path.dirname(dst)
        if parent:
            os.makedirs(parent, exist_ok=True)
        shutil.move(src, dst)
        return {"src": src, "dst": dst, "moved": True}
    except Exception as e:
        return {"src": src, "dst": dst, "error": str(e)}

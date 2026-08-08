"""
file_ingest.py — Turns an uploaded file's raw bytes into text Jarvis can actually read, or
honestly reports that it can't. No new pip dependency: this covers plain text and code
files (the overwhelming majority of "notes/docs/data" someone attaches to a chat) via
stdlib decoding only. Binary formats (PDF, docx, images, zips, ...) are stored — the
attachment still shows up, still counts toward the file — but content_text stays None and
extractable=False, so the UI can say so plainly instead of pretending nothing was uploaded.

Deliberately NOT trying to be a universal document parser (no pypdf/python-docx/etc. added
here) — that's a real, scoped follow-up if it turns out to matter, not something to bolt on
silently as a side effect of a much bigger feature request.
"""

import os

TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".py", ".js", ".jsx", ".ts", ".tsx", ".json",
    ".yaml", ".yml", ".csv", ".tsv", ".log", ".html", ".htm", ".css", ".xml", ".ini",
    ".cfg", ".conf", ".sh", ".bash", ".ps1", ".sql", ".java", ".c", ".cpp", ".h", ".hpp",
    ".cs", ".rs", ".go", ".rb", ".php", ".env", ".toml", ".gitignore", ".dockerfile",
}

MAX_CHARS_PER_FILE = 20_000        # per-file cap before content is truncated
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5MB cap on what's even accepted


def extract_text(filename: str, raw: bytes):
    """Returns (content_text_or_None, extractable: bool, truncated: bool)."""
    ext = os.path.splitext(filename)[1].lower()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        if ext not in TEXT_EXTENSIONS:
            return None, False, False
        # A known text/code extension that isn't clean UTF-8 (odd encoding, stray bytes) —
        # still worth showing something rather than refusing the whole file.
        text = raw.decode("utf-8", errors="replace")

    truncated = False
    if len(text) > MAX_CHARS_PER_FILE:
        text = text[:MAX_CHARS_PER_FILE]
        truncated = True
    return text, True, truncated


def build_context_block(files: list, max_total_chars: int = 60_000) -> str:
    """Renders a list of file rows (conversation_store.get_context_files() shape) into the
    block folded into the system prompt. Stops adding files once the total budget is hit
    rather than blowing out the model's context window — later files just get a one-line
    'omitted, budget reached' note instead of being silently dropped without explanation."""
    if not files:
        return ""

    parts = []
    used = 0
    omitted = []
    for f in files:
        if not f.get("extractable") or not f.get("content_text"):
            parts.append(f"- {f['filename']}: (not a readable text format — attached but not shown here)")
            continue
        content = f["content_text"]
        if used + len(content) > max_total_chars:
            omitted.append(f["filename"])
            continue
        used += len(content)
        note = " [truncated]" if f.get("truncated") else ""
        parts.append(f"--- {f['filename']}{note} ---\n{content}")

    if omitted:
        parts.append(f"(also attached, omitted here — total context budget reached: {', '.join(omitted)})")

    return "\n\n".join(parts)

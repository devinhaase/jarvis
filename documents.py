"""
documents.py — document intelligence: drop a manual, receipt, or warranty into
data/documents/, ask about it later, get an answer that cites which file it came from.
The one deliberate gap left by file_ingest.py's own docstring ("NOT trying to be a
universal document parser... a real, scoped follow-up if it turns out to matter") — this
is that follow-up, scoped specifically to a standing local document library rather than
bolted onto per-message chat attachments, which stay exactly as they were.

Reuses embeddings.py (already there for semantic conversation search, itself reusing the
local Ollama instance rather than a new ML dependency) for the actual retrieval. Adds
pypdf as this feature's one new dependency — manuals/receipts are overwhelmingly PDFs in
practice, and file_ingest.py explicitly punted on that format; a plain-text-only document
library wouldn't cover the actual use case.

Design, deliberately simple:
  - data/documents/ — where you drop files. Nothing watches it continuously; reindex()
    runs lazily at the top of every search_documents() call, and only re-embeds a file
    whose mtime changed since it was last indexed (or is new), so a folder of 30 manuals
    that haven't changed costs one stat() call each per search, not 30 embedding calls.
  - data/document_index.json — flat list of chunks: {document, chunk_index, text,
    embedding, mtime}. A JSON file, not a new SQLite db — this is read-in-full and
    re-ranked in Python on every search anyway (no query language needed for a personal
    document library sized in the tens, not thousands, of files), so there's nothing a
    database would buy here that a list doesn't already give for free.
  - No embedding available (Ollama down, model not pulled)? Same convention
    embeddings.py's own docstring establishes for every other caller: degrade to a plain
    keyword substring search over the chunk text, never hard-fail.
"""

import os
import re
import json
import time

import embeddings

DOCS_DIR = os.path.join("data", "documents")
INDEX_FILE = os.path.join("data", "document_index.json")

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150

TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".rst", ".csv", ".log"}


def _read_pdf(path: str):
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(pages).strip() or None
    except Exception:
        return None  # encrypted, corrupt, or an image-only scan with no text layer


def _read_text_file(path: str):
    try:
        with open(path, "rb") as f:
            raw = f.read()
        return raw.decode("utf-8", errors="replace").strip() or None
    except Exception:
        return None


def _extract_document_text(path: str):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return _read_pdf(path)
    if ext in TEXT_EXTENSIONS:
        return _read_text_file(path)
    return None  # unsupported format — skipped, not an error (see reindex()'s "skipped" list)


def _chunk_text(text: str) -> list:
    """Character-based sliding window, not sentence/token-aware — good enough for a
    manual/receipt's prose and much simpler than a real tokenizer-driven splitter. Overlap
    means a fact sitting right on a chunk boundary still appears whole in at least one
    chunk rather than being split across two half-useless ones."""
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= CHUNK_SIZE:
        return [text] if text else []
    chunks = []
    start = 0
    while start < len(text):
        end = start + CHUNK_SIZE
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = end - CHUNK_OVERLAP
    return chunks


def _load_index() -> list:
    if not os.path.exists(INDEX_FILE):
        return []
    try:
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_index(chunks: list):
    os.makedirs(os.path.dirname(INDEX_FILE) or ".", exist_ok=True)
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(chunks, f)


def reindex() -> dict:
    """Scans DOCS_DIR, re-embeds anything new or changed (by mtime), drops chunks for
    files no longer present. Returns a summary — never raises; a single unreadable file
    just lands in "skipped" rather than aborting the whole reindex."""
    os.makedirs(DOCS_DIR, exist_ok=True)
    existing = _load_index()
    by_doc = {}
    for chunk in existing:
        by_doc.setdefault(chunk["document"], []).append(chunk)

    on_disk = set()
    updated_chunks = []
    reindexed, skipped, unchanged = [], [], []
    embeddings_ok = embeddings.is_available()

    for name in sorted(os.listdir(DOCS_DIR)):
        path = os.path.join(DOCS_DIR, name)
        if not os.path.isfile(path):
            continue
        on_disk.add(name)
        mtime = os.path.getmtime(path)

        prior = by_doc.get(name)
        if prior and prior[0].get("mtime") == mtime:
            updated_chunks.extend(prior)
            unchanged.append(name)
            continue

        text = _extract_document_text(path)
        if text is None:
            skipped.append(name)
            continue

        for i, chunk_text in enumerate(_chunk_text(text)):
            updated_chunks.append({
                "document": name,
                "chunk_index": i,
                "text": chunk_text,
                "embedding": embeddings.embed_text(chunk_text) if embeddings_ok else None,
                "mtime": mtime,
            })
        reindexed.append(name)

    # anything in by_doc but not on_disk was deleted — already excluded by only carrying
    # forward chunks for names we actually saw above, so nothing further needed here.
    _save_index(updated_chunks)

    return {
        "reindexed": reindexed, "unchanged": unchanged, "skipped": skipped,
        "total_documents": len(on_disk), "total_chunks": len(updated_chunks),
        "embeddings_available": embeddings_ok,
    }


def search_documents(query: str, top_k: int = 5) -> dict:
    """Reindexes (cheap — see reindex()'s own docstring) then returns the top_k most
    relevant chunks, each naming its source document so an answer can cite it. Falls back
    to a plain substring/keyword score when embeddings aren't available rather than
    failing outright."""
    query = (query or "").strip()
    if not query:
        return {"error": "query cannot be empty"}

    summary = reindex()
    chunks = _load_index()
    if not chunks:
        return {
            "results": [],
            "note": f"No documents indexed yet — drop files into {DOCS_DIR} and try again.",
        }

    query_embedding = embeddings.embed_text(query) if summary["embeddings_available"] else None

    scored = []
    if query_embedding is not None:
        for c in chunks:
            if c.get("embedding") is None:
                continue
            scored.append((embeddings.cosine_similarity(query_embedding, c["embedding"]), c))
    else:
        # Keyword fallback: score by how many distinct query words appear in the chunk,
        # case-insensitive — crude, but "no embeddings, no search at all" would be worse.
        words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2]
        for c in chunks:
            text_lower = c["text"].lower()
            score = sum(1 for w in words if w in text_lower)
            if score > 0:
                scored.append((float(score), c))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    results = [
        {
            "document": c["document"],
            "excerpt": c["text"][:600],
            "score": round(score, 4),
        }
        for score, c in scored[:top_k]
    ]
    return {"results": results, "documents_indexed": summary["total_documents"]}


def list_documents() -> dict:
    """What's currently in the library, without triggering a reindex — a quick inventory
    check, not a search."""
    os.makedirs(DOCS_DIR, exist_ok=True)
    files = sorted(
        name for name in os.listdir(DOCS_DIR)
        if os.path.isfile(os.path.join(DOCS_DIR, name))
    )
    return {"documents": files, "count": len(files), "folder": DOCS_DIR}

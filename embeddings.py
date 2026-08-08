"""
embeddings.py — Local semantic embeddings via Ollama's /api/embeddings endpoint. Reuses
infrastructure that's already there (Ollama, already running locally for chat) instead of
adding a new heavy ML dependency (sentence-transformers + torch) just for embeddings.

Zero-API-key by construction — this only ever talks to the local Ollama instance
(OLLAMA_URL, same var llm.py's OllamaLlm uses). If Ollama isn't running or the embedding
model isn't pulled, embed_text() returns None rather than raising — every caller treats
"no embedding available" as "semantic search degrades to keyword search only", not a hard
failure. See conversation_store.py's semantic-search methods and server.py's background
population hook.
"""

import os
import math

EMBEDDING_MODEL = os.getenv("JARVIS_EMBEDDING_MODEL", "nomic-embed-text")


def embed_text(text: str):
    """Returns a list[float] embedding, or None if Ollama/the embedding model isn't
    available. `text` is truncated to a reasonable length — embedding models have their
    own context limits and a full multi-KB message doesn't need to be sent whole to get a
    useful semantic vector for search purposes."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        import requests
        url = f"{os.getenv('OLLAMA_URL', 'http://localhost:11434')}/api/embeddings"
        r = requests.post(url, json={"model": EMBEDDING_MODEL, "prompt": text[:4000]}, timeout=30)
        r.raise_for_status()
        embedding = r.json().get("embedding")
        return embedding if embedding else None
    except Exception:
        return None


def cosine_similarity(a: list, b: list) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def is_available() -> bool:
    """Cheap check: does embed_text() actually work right now? Used to decide whether to
    even attempt background population / offer semantic search as an option."""
    return embed_text("availability check") is not None

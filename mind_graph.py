"""
mind_graph.py — Phase 8c, "Living Mind": builds the node/edge snapshot the 3D visualizer
(webapp/mind.html + mind.js) renders. Pure data-shaping, no websocket/three.js concerns here
— testable on its own, same separation session_manager.py/conversation_store.py already keep
from server.py's transport layer.

Adaptation from the source spec, stated plainly rather than silently: that spec assumes a
literal multi-agent architecture (named sub-agents, each with tools/avatar). Jarvis doesn't
have that — it's one coordinator with a tagged tool registry (general/defense/offense/
practice, see tools.py's `role`). Rather than inventing agents that don't exist, this graph
represents that real taxonomy as four "specialist hub" nodes between the core and their
tools — an honest structural stand-in, not a fabrication. If Jarvis ever grows literal
sub-agents, this is the seam where they'd replace the hubs.

Regions in the resulting graph:
  - core: exactly one "Jarvis" node.
  - hub: one per tool role (general/defense/offense/practice) — the specialist-taxonomy
    stand-in described above.
  - tool: one per entry in tools.ALL_TOOLS, attached to its role's hub.
  - memory: one per recent conversation (attached to a "Working Memory" hub off core),
    sized/labeled from real conversation_store data.
  - semantic edges: conversation-to-conversation links where their centroid embeddings
    (conversation_store.get_conversation_centroids(), Phase 8c) exceed a similarity
    threshold — the same real embedding infrastructure Phase 7's semantic search uses, not
    a new one.
"""

import time

_ROLE_LABELS = {
    "general": "General",
    "defense": "Defense",
    "offense": "Offense",
    "practice": "Practice",
}
_ROLE_COLORS = {
    "general": "#0a84ff",   # matches the app's existing accent (index.html theme-color)
    "defense": "#30d158",
    "offense": "#ff453a",
    "practice": "#bf5af2",
}

DEFAULT_CONVERSATION_LIMIT = 40
DEFAULT_SIMILARITY_THRESHOLD = 0.80


def _tool_nodes_and_edges():
    from tools import ALL_TOOLS

    nodes = [{"id": "core", "type": "core", "label": "Jarvis"}]
    edges = []

    roles_present = set()
    for tool in ALL_TOOLS.values():
        roles_present.add(tool.role or "general")

    for role in ("general", "defense", "offense", "practice"):
        if role not in roles_present:
            continue
        hub_id = f"hub:{role}"
        nodes.append({
            "id": hub_id, "type": "hub", "label": _ROLE_LABELS[role],
            "role": role, "color": _ROLE_COLORS[role],
        })
        edges.append({"source": "core", "target": hub_id, "type": "structural"})

    for name, tool in sorted(ALL_TOOLS.items()):
        role = tool.role or "general"
        node_id = f"tool:{name}"
        nodes.append({
            "id": node_id, "type": "tool", "label": name,
            "role": role, "tier": tool.tier.name,
            "description": tool.description,
            "color": _ROLE_COLORS[role],
        })
        edges.append({"source": f"hub:{role}", "target": node_id, "type": "structural"})

    return nodes, edges


def _memory_nodes_and_edges(conversation_limit: int, similarity_threshold: float):
    from conversation_store import store

    nodes = [{"id": "hub:memory", "type": "hub", "label": "Working Memory", "color": "#ffd60a"}]
    edges = [{"source": "core", "target": "hub:memory", "type": "structural"}]

    conversations = store.list_conversations(limit=conversation_limit)
    if not conversations:
        return nodes, edges

    for conv in conversations:
        node_id = f"conv:{conv['id']}"
        nodes.append({
            "id": node_id, "type": "memory", "label": conv.get("title") or "Untitled",
            "conversation_id": conv["id"], "updated_at": conv.get("updated_at"),
            "project_id": conv.get("project_id"),
        })
        edges.append({"source": "hub:memory", "target": node_id, "type": "structural"})

    # Semantic edges — real centroid embeddings, degrades to "no edges" (not an error) if
    # nothing's embedded yet or Ollama's embedding model isn't available, same convention
    # as every other embedding-dependent feature in this codebase.
    try:
        ids = [c["id"] for c in conversations]
        centroids = store.get_conversation_centroids(ids)
        if len(centroids) >= 2:
            from embeddings import cosine_similarity
            items = list(centroids.items())
            for i in range(len(items)):
                for j in range(i + 1, len(items)):
                    cid_a, vec_a = items[i]
                    cid_b, vec_b = items[j]
                    sim = cosine_similarity(vec_a, vec_b)
                    if sim >= similarity_threshold:
                        edges.append({
                            "source": f"conv:{cid_a}", "target": f"conv:{cid_b}",
                            "type": "semantic", "weight": round(sim, 4),
                        })
    except Exception:
        pass  # semantic layer is best-effort — the structural graph still renders without it

    return nodes, edges


def build_snapshot(conversation_limit: int = DEFAULT_CONVERSATION_LIMIT,
                    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD) -> dict:
    """The full initial graph a freshly-connected /ws/mind client renders. Live updates after
    that come as small incremental events (tool_fired, memory_pulse) from server.py, not by
    re-fetching this whole snapshot."""
    tool_nodes, tool_edges = _tool_nodes_and_edges()
    memory_nodes, memory_edges = _memory_nodes_and_edges(conversation_limit, similarity_threshold)

    return {
        "generated_at": time.time(),
        "nodes": tool_nodes + memory_nodes,
        "edges": tool_edges + memory_edges,
    }

"""
server.py — The multi-device "central brain": one long-running process holding shared
memory/state and now shared conversation history, reachable from multiple thin clients
over a WebSocket. Run it once on your network; every device — the web GUI, the CLI thin
client (main.py --server), the voice companion (voice_companion.py) — connects to it
instead of running its own local Coordinator.

Usage:
    Register a device once (prints its token — copy it into that device's config, it is not
    stored anywhere you can retrieve it again):
        python server.py --register desktop-cli --name "Devin's Desktop" --capabilities filesystem,microphone

    Run the server:
        python server.py
    (host/port come from JARVIS_SERVER_HOST / JARVIS_SERVER_PORT in .env, default 0.0.0.0:8765)
    Open http://<host>:<port>/ in a browser for the web GUI once running.

Design notes (Phase 3 — see task.md for the full history):
  - One shared Memory instance and one shared JarvisBrain ("the central brain") are created
    once at startup. A JarvisBrain is stateless with respect to any one conversation — the
    ReAct loop takes chat_history as a parameter rather than holding it — so unlike Phase 2d
    there's no need for one brain per connected session; every turn from every device reuses
    the same brain, same approval_fn, same Coordinator.
  - Conversations are the unit of persistence now (conversation_store.py, SQLite), not
    per-connection sessions. A websocket attaches to exactly one conversation_id at a time
    (set at hello, changeable mid-connection via open_conversation/new_conversation) — this
    is what lets a browser tab and the voice companion watch and contribute to the SAME
    conversation live, and lets a browser tab switch conversations without reconnecting.
  - Only the human-visible turns (the user's message, the final assistant reply) are
    persisted. The ReAct loop's intermediate tool-round scaffolding ("[SYSTEM] Tool
    execution complete...") lives only in the in-memory chat_history for that one turn's
    LLM context, then is discarded — the next turn rebuilds chat_history fresh from the
    store. That scaffolding was never meant to be read as chat history; persisting it (as
    Phase 2d's flat session files did) just meant a future GUI would have had to hide it.
  - Streaming: JarvisBrain.process_turn_stream() is a synchronous generator (it calls a
    blocking LLM API and, mid-turn, blocking tool subprocess calls) — it runs in a worker
    thread, and each yielded event is handed back onto the event loop via
    asyncio.run_coroutine_threadsafe() + a queue, so it can be broadcast to every websocket
    currently watching that conversation as it happens, not just returned once at the end.
  - Device capability handshake, Tier-3/4 approval broadcast/timeout defaults: unchanged
    from Phase 2d — see make_approval_fn() below. Approval requests still broadcast to
    EVERY connected device regardless of which conversation they're watching (deliberately
    not narrowed to conversation watchers — you should be able to approve a Tier-4 action
    from any device, not only one that happens to have that conversation open).
"""

import os
import sys

# A server has no guarantee of a UTF-8-capable console — it may be launched from a legacy
# Windows codepage shell, a service manager, or nothing interactive at all. Reconfigure
# stdout/stderr to UTF-8 with a replace-not-raise error policy so a logging side effect can
# never take down real work, regardless of what launched this process. (coordinator.py now
# does this too, at the actual source of the emoji prints — this stays here as well since
# server.py prints its own status lines before coordinator is even imported.)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import json
import time
import uuid
import asyncio
import argparse
import threading
import re
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from memory import Memory
from tools import Tier, ALL_TOOLS
from session_manager import JarvisBrain
from device_registry import DeviceRegistry
from conversation_store import store
from security_hardening import AuthRateLimiter

WEBAPP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp")
WEB_GUI_DEVICE_ID = "web-gui"
WEB_GUI_TOKEN_FILE = os.path.join("data", ".web_gui_token")

APPROVAL_TIMEOUT = {"TIER_3": 3.0, "TIER_4": 60.0}
APPROVAL_DEFAULT = {"TIER_3": True, "TIER_4": False}  # what happens if nobody answers in time

registry = DeviceRegistry()
_auth_limiter = AuthRateLimiter()  # Phase 8: lockout after repeated failed device-auth attempts
shared_memory = Memory()  # the one long-term memory every session reads/writes


def _ensure_web_gui_device() -> str:
    """The browser GUI needs a device token like any other client, but there's no one to
    hand-type it in — the whole point is that opening the page just works. So: provision a
    'web-gui' device once, on this machine, the first time the server ever runs, and cache
    its token in data/.web_gui_token (same trust level as devices.json itself, which already
    holds every device's plaintext token — this isn't a new class of secret). Grants
    'filesystem' only: tools tagged with it always execute wherever the SERVER runs, not
    wherever the browser is, so this just lets the desktop's own GUI use desktop tools —
    the same thing the desktop-cli device already does.

    The unauthenticated GET /gui-config endpoint that hands this token to the page is a
    deliberate, bounded trade-off: anyone who can already reach this server on your LAN
    could read it, but they could already reach every route this app serves at that point —
    this app's threat model has always been "trusted home LAN", not "hostile network"."""
    if os.path.exists(WEB_GUI_TOKEN_FILE):
        with open(WEB_GUI_TOKEN_FILE, 'r') as f:
            token = f.read().strip()
        if token and registry.validate(WEB_GUI_DEVICE_ID, token) is not None:
            return token
    token = registry.register(WEB_GUI_DEVICE_ID, name="Web GUI", capabilities=["filesystem"])
    os.makedirs(os.path.dirname(WEB_GUI_TOKEN_FILE) or ".", exist_ok=True)
    with open(WEB_GUI_TOKEN_FILE, 'w') as f:
        f.write(token)
    return token

# Runtime state — all accessed only from the main event loop thread except where noted.
connected = {}      # websocket -> {"device_id", "capabilities", "conversation_id"}
watchers = {}        # conversation_id -> set(websocket) currently attached to it
pending_approvals = {}    # approval_id -> asyncio.Future
main_loop = None          # captured at startup so worker threads can schedule back onto it
brain = None              # the one shared JarvisBrain — created in _lifespan, after main_loop exists
mind_watchers = set()     # websockets connected to /ws/mind (Phase 8c, Living Mind — read-only observers)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    global main_loop, brain
    main_loop = asyncio.get_running_loop()
    brain = JarvisBrain(approval_fn=make_approval_fn(), memory=shared_memory, conversation_store=store)
    _ensure_web_gui_device()

    monitor_task = None
    try:
        from posture_monitor import posture_monitor_loop
        monitor_task = asyncio.create_task(posture_monitor_loop(broadcast_all=_broadcast_all))
    except Exception as e:
        print(f"[Server] Defense posture monitor not started: {e}")

    briefing_task = None
    try:
        from daily_briefing import daily_briefing_loop
        briefing_task = asyncio.create_task(daily_briefing_loop(brain=brain, broadcast_all=_broadcast_all))
    except Exception as e:
        print(f"[Server] Daily briefing loop not started: {e}")

    team_board_task = None
    try:
        from team_board_dispatcher import team_board_dispatch_loop
        team_board_task = asyncio.create_task(team_board_dispatch_loop(brain=brain, broadcast_all=_broadcast_all))
    except Exception as e:
        print(f"[Server] Team board dispatcher not started: {e}")

    yield

    if monitor_task:
        monitor_task.cancel()
    if briefing_task:
        briefing_task.cancel()
    if team_board_task:
        team_board_task.cancel()


app = FastAPI(title="Jarvis Server", lifespan=_lifespan)


# ---------------------------------------------------------------------------
# Broadcast helpers
# ---------------------------------------------------------------------------

def _detach(ws):
    info = connected.pop(ws, None)
    if info:
        cid = info.get("conversation_id")
        if cid and cid in watchers:
            watchers[cid].discard(ws)
            if not watchers[cid]:
                del watchers[cid]


async def _broadcast_watchers(conversation_id: str, payload: dict, exclude=None):
    dead = []
    for ws in list(watchers.get(conversation_id, set())):
        if ws is exclude:
            continue
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _detach(ws)


async def _broadcast_all(payload: dict):
    dead = []
    for ws in list(connected.keys()):
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _detach(ws)


async def _broadcast_mind(payload: dict):
    """Phase 8c — pushes a live event to every connected Living Mind observer. Separate
    from _broadcast_all/_broadcast_watchers by design: mind_watchers is a different set of
    sockets (read-only visualization clients, not conversation participants), and a failure
    to reach one should never affect a real conversation turn."""
    dead = []
    for ws in list(mind_watchers):
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        mind_watchers.discard(ws)


# ---------------------------------------------------------------------------
# Approval broadcast — bridges a worker thread (running Coordinator.run_tools) back onto
# the event loop, so a Tier-3/4 confirmation can be answered by any connected device.
# ---------------------------------------------------------------------------

async def _wait_for_approval(action_name: str, tier_name: str) -> bool:
    approval_id = uuid.uuid4().hex
    future = main_loop.create_future()
    pending_approvals[approval_id] = future
    await _broadcast_all({
        "type": "approval_request", "approval_id": approval_id,
        "action": action_name, "tier": tier_name,
    })
    try:
        return await asyncio.wait_for(future, timeout=APPROVAL_TIMEOUT.get(tier_name, 30.0))
    except asyncio.TimeoutError:
        return APPROVAL_DEFAULT.get(tier_name, False)
    finally:
        pending_approvals.pop(approval_id, None)


def make_approval_fn():
    """Returns a sync callable(action_name, tier) -> bool suitable for Coordinator's
    approval_fn — called from a worker thread, so it hops onto the main event loop with
    run_coroutine_threadsafe and blocks that worker thread until resolved, exactly
    mirroring the CLI's blocking Confirm.ask()/time.sleep() from the caller's point of
    view, just answered by a remote device instead of local stdin."""

    def approval_fn(action_name, tier) -> bool:
        if tier not in (Tier.TIER_3, Tier.TIER_4):
            return True
        tier_name = tier.name
        fut = asyncio.run_coroutine_threadsafe(_wait_for_approval(action_name, tier_name), main_loop)
        try:
            return fut.result(timeout=APPROVAL_TIMEOUT.get(tier_name, 30.0) + 5)
        except Exception:
            return APPROVAL_DEFAULT.get(tier_name, False)

    return approval_fn


def _skill_to_dict(skill) -> dict:
    return {
        "name": skill.name, "status": skill.status, "tier": skill.tier, "team": skill.team,
        "version": skill.version, "success_count": skill.success_count, "fail_count": skill.fail_count,
        "flagged": skill.flagged, "when_to_use": skill.when_to_use, "steps": skill.steps,
        "tools": skill.tools, "created_at": skill.created_at, "updated_at": skill.updated_at,
    }


def _team_catalog() -> list:
    """The 5 subagent teams (Phase 4), for the GUI's Teams panel — same read-only,
    nothing-secret posture as _tool_catalog(). tool_count uses each team's live
    tool_names() (owned + coordinator-level) rather than a static number, so it never
    drifts from what a team can actually call."""
    from teams import TEAMS
    return [
        {
            "key": key, "name": team.display_name, "scope_prompt": team.scope_prompt,
            "tool_count": len(team.tool_names()), "aliases": team.aliases,
        }
        for key, team in TEAMS.items()
    ]


def _tool_catalog() -> list:
    """Everything Jarvis can do, for the GUI's Tools panel — read-only, no secrets, just
    what's already visible in the system prompt anyway (get_tool_descriptions() in llm.py)."""
    return sorted((
        {"name": name, "description": tool.description, "tier": tool.tier.name, "role": tool.role}
        for name, tool in ALL_TOOLS.items()
    ), key=lambda t: (t["role"] or "", t["name"]))


# ---------------------------------------------------------------------------
# Streaming turn execution — bridges team_router.route_and_run() (a blocking generator,
# itself built on JarvisBrain.process_turn_stream(), one call per routed team — see
# team_router.py) onto the event loop via a worker thread + asyncio.Queue, broadcasting
# each event to every websocket currently watching the conversation as it happens.
# Phase 4: this used to call brain.process_turn_stream() directly for a single flat-tool
# loop; team_router wraps that same engine per-team now, but yields the identical
# (kind, payload) event shape, so nothing below this point needed to change.
# ---------------------------------------------------------------------------

async def _run_turn(conversation_id: str, chat_history: list, capabilities: list):
    loop = asyncio.get_running_loop()
    q = asyncio.Queue()

    def _worker():
        def _on_status(event, data):
            asyncio.run_coroutine_threadsafe(q.put(("status", {"event": event, "data": data})), loop).result()

        try:
            import team_router
            for kind, payload in team_router.route_and_run(
                brain, chat_history, capabilities, conversation_id=conversation_id, on_status=_on_status
            ):
                asyncio.run_coroutine_threadsafe(q.put((kind, payload)), loop).result()
        except Exception as e:
            asyncio.run_coroutine_threadsafe(q.put(("error", str(e))), loop).result()
        finally:
            asyncio.run_coroutine_threadsafe(q.put(("__end__", None)), loop).result()

    threading.Thread(target=_worker, daemon=True).start()

    result = {"response": "", "tools_ran": [], "denied": []}
    while True:
        kind, payload = await q.get()
        if kind == "__end__":
            break
        if kind == "status":
            await _broadcast_watchers(conversation_id, {
                "type": "tool_status", "conversation_id": conversation_id,
                "event": payload.get("event"), "data": payload.get("data"),
            })
            if payload.get("event") == "tools_starting":
                # Phase 8c: pulse the corresponding tool node(s) in the Living Mind view the
                # moment execution actually begins — same signal the conversation GUI uses,
                # just fanned out to a second, independent set of observers.
                await _broadcast_mind({"type": "tool_fired", "tools": payload.get("data") or []})
        elif kind == "chunk":
            await _broadcast_watchers(conversation_id, {
                "type": "stream_chunk", "conversation_id": conversation_id, "text": payload
            })
        elif kind == "round_end":
            await _broadcast_watchers(conversation_id, {
                "type": "round_end", "conversation_id": conversation_id, **payload
            })
        elif kind == "done":
            result = payload
        elif kind == "error":
            result = {"response": f"Internal error: {payload}", "tools_ran": [], "denied": []}

    return result


# ---------------------------------------------------------------------------
# Auto-titling — one small extra LLM call after the first exchange in a fresh conversation.
# ---------------------------------------------------------------------------

def _generate_title(user_text: str, assistant_text: str):
    if brain is None or brain._brain is None:
        return None
    from llm import stream_and_filter_tags
    prompt = (
        "Give this exchange a short title: 3-6 words, plain text, no quotes, no trailing "
        "punctuation. Reply with ONLY the title, nothing else.\n\n"
        f"User: {user_text[:300]}\nAssistant: {assistant_text[:300]}"
    )
    try:
        raw = "".join(brain._brain.chat_stream(
            [{"role": "user", "content": prompt}],
            "You generate short, plain conversation titles. Output only the title."
        ))
        visible = "".join(stream_and_filter_tags([raw])).strip().strip('"').strip()
        return visible[:60] if visible else None
    except Exception:
        return None


async def _embed_message_background(message_id: int, conversation_id: str, text: str):
    """Fire-and-forget: compute and store a message's semantic embedding without making the
    turn that produced it wait on an extra Ollama round trip. If Ollama/the embedding model
    isn't available, embed_text() returns None and this just no-ops — semantic_search then
    simply has less (or nothing) to search over, never a hard failure anywhere else."""
    from embeddings import embed_text, EMBEDDING_MODEL
    vector = await asyncio.to_thread(embed_text, text)
    if vector:
        store.add_message_embedding(message_id, conversation_id, EMBEDDING_MODEL, vector)


async def _maybe_autotitle(conversation_id: str):
    if store.is_titled(conversation_id) or store.message_count(conversation_id) < 2:
        return
    history = store.get_chat_history(conversation_id)
    first_user = next((m["content"] for m in history if m["role"] == "user"), "")
    first_assistant = next((m["content"] for m in history if m["role"] == "assistant"), "")
    title = await asyncio.to_thread(_generate_title, first_user, first_assistant)
    if not title:
        title = (first_user[:50] or "New conversation")
    store.rename_conversation(conversation_id, title)
    await _broadcast_all({"type": "conversation_renamed", "conversation_id": conversation_id, "title": title})


async def _maybe_reflect(user_message: str, result: dict):
    """Phase 5 — fire-and-forget after a non-trivial turn (see reflection.should_reflect).
    Never touches what Devin already saw; a proposed skill (if any) just lands in SKILLS/
    with status='proposed' for later review, same posture as _maybe_autotitle/
    _maybe_update_summary firing after the response is already sent."""
    from reflection import should_reflect, run_reflection
    tools_ran = result.get("tools_ran", [])
    denied = result.get("denied", [])
    if not should_reflect(tools_ran, denied):
        return
    team_key = None
    teams_involved = result.get("teams_involved") or []
    if teams_involved:
        team_key = teams_involved[-1]  # the team whose loop actually ran the tools in question
    try:
        await asyncio.to_thread(
            run_reflection, brain, user_message, tools_ran, result.get("response", ""), team_key
        )
    except Exception as e:
        print(f"[Server] Reflection pass failed: {e}")


def _generate_summary_update(prior_summary: str, new_messages: list):
    if brain is None or brain._brain is None:
        return None
    from llm import stream_and_filter_tags
    text_block = "\n".join(f"{m['role']}: {m['content'][:500]}" for m in new_messages)
    prompt = (
        "Update the running summary of this conversation to fold in the new messages below "
        "— extend/revise the existing summary, don't discard it and start over. Keep it "
        "compact (a paragraph or two): capture facts, decisions, and context that would "
        "matter for answering future questions in this conversation, not a blow-by-blow "
        "transcript of what was said.\n\n"
        f"EXISTING SUMMARY:\n{prior_summary or '(none yet — this is the first summarization pass)'}\n\n"
        f"NEW MESSAGES TO FOLD IN:\n{text_block[:6000]}"
    )
    try:
        raw = "".join(brain._brain.chat_stream(
            [{"role": "user", "content": prompt}], "You write compact, cumulative conversation summaries."
        ))
        return "".join(stream_and_filter_tags([raw])).strip()
    except Exception:
        return None


async def _maybe_update_summary(conversation_id: str, recent_n: int = 20):
    """Keeps a rolling summary of a conversation's older messages so get_llm_context_history
    doesn't have to resend the entire history every turn once a conversation gets long. Only
    does anything once there's actually a real batch of older messages to fold in — not
    every single turn, which would mean constant extra LLM calls for one message at a time."""
    to_summarize = store.get_messages_to_summarize(conversation_id, recent_n=recent_n)
    if not to_summarize:
        return
    conv = store.get_conversation(conversation_id)
    prior_summary = (conv or {}).get("summary") or ""
    new_summary = await asyncio.to_thread(_generate_summary_update, prior_summary, to_summarize)
    if new_summary:
        through_id = to_summarize[-1]["id"]
        store.set_conversation_summary(conversation_id, new_summary, through_id)


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    # --- Handshake: first message must be {"type": "hello", ...} ---
    try:
        hello = await asyncio.wait_for(websocket.receive_json(), timeout=15)
    except Exception:
        await websocket.close(code=4000, reason="Expected a hello handshake within 15s")
        return

    if hello.get("type") != "hello":
        await websocket.close(code=4000, reason="First message must be type=hello")
        return

    device_id = hello.get("device_id", "")
    token = hello.get("token", "")
    requested_caps = set(hello.get("capabilities", []))

    # Phase 8: rate-limit auth attempts per source IP before even checking the token —
    # devices.json tokens are compared in constant time (no timing side-channel) but that
    # alone never stopped unlimited guessing. Keyed by IP, not device_id, since a bad guess
    # doesn't reliably know a real device_id either.
    client_host = websocket.client.host if websocket.client else "unknown"
    locked_until = _auth_limiter.is_locked(client_host)
    if locked_until:
        wait_s = int(locked_until - time.time())
        await websocket.send_json({"type": "error", "message": f"Too many failed auth attempts. Try again in {wait_s}s."})
        await websocket.close(code=4429, reason="Rate limited")
        return

    allowed_caps = registry.validate(device_id, token)
    if allowed_caps is None:
        _auth_limiter.record_failure(client_host)
        await websocket.send_json({"type": "error", "message": "Unknown device or invalid token."})
        await websocket.close(code=4001, reason="Unauthorized")
        return
    _auth_limiter.record_success(client_host)

    # Never trust what the client claims beyond what this device was registered for.
    capabilities = list(requested_caps & set(allowed_caps))

    conversation_id = hello.get("conversation_id")
    if not conversation_id or not store.conversation_exists(conversation_id):
        conversation_id = store.create_conversation(device_id=device_id)

    connected[websocket] = {"device_id": device_id, "capabilities": capabilities, "conversation_id": conversation_id}
    watchers.setdefault(conversation_id, set()).add(websocket)

    await websocket.send_json({
        "type": "ready", "conversation_id": conversation_id, "capabilities": capabilities,
        "brain_available": brain.is_available() if brain else False,
        "resumed_turns": store.message_count(conversation_id),
        "project_id": (store.get_conversation(conversation_id) or {}).get("project_id"),
    })

    try:
        while True:
            msg = await websocket.receive_json()
            mtype = msg.get("type")
            info = connected.get(websocket)
            if info is None:
                break

            if mtype == "approve":
                approval_id = msg.get("approval_id")
                fut = pending_approvals.get(approval_id)
                if fut and not fut.done():
                    fut.set_result(bool(msg.get("approved", False)))
                continue

            if mtype == "voice_status":
                await _broadcast_watchers(info["conversation_id"], {
                    "type": "voice_status", "conversation_id": info["conversation_id"],
                    "state": msg.get("state", "idle"), "device_id": info["device_id"],
                })
                continue

            if mtype == "list_conversations":
                await websocket.send_json({
                    "type": "conversation_list",
                    "conversations": store.list_conversations(project_id=msg.get("project_id")),
                    "project_id": msg.get("project_id"),
                })
                continue

            if mtype == "search_conversations":
                query = msg.get("query", "")
                await websocket.send_json({
                    "type": "search_results", "query": query, "results": store.search(query)
                })
                continue

            if mtype == "semantic_search":
                query = msg.get("query", "")
                from embeddings import embed_text
                query_vec = await asyncio.to_thread(embed_text, query)
                if query_vec is None:
                    await websocket.send_json({
                        "type": "search_results", "query": query, "results": [],
                        "note": "Semantic search unavailable — Ollama or the embedding model isn't reachable right now.",
                    })
                else:
                    results = await asyncio.to_thread(store.semantic_search, query_vec)
                    await websocket.send_json({"type": "search_results", "query": query, "results": results})
                continue

            if mtype == "list_projects":
                await websocket.send_json({"type": "project_list", "projects": store.list_projects()})
                continue

            if mtype == "create_project":
                pid = store.create_project(msg.get("name", ""))
                await _broadcast_all({"type": "project_list", "projects": store.list_projects()})
                await websocket.send_json({"type": "project_created", "project_id": pid})
                continue

            if mtype == "rename_project":
                pid = msg.get("project_id")
                name = msg.get("name", "")
                if pid and store.project_exists(pid) and name.strip():
                    store.rename_project(pid, name)
                    await _broadcast_all({"type": "project_list", "projects": store.list_projects()})
                continue

            if mtype == "delete_project":
                pid = msg.get("project_id")
                if pid and store.project_exists(pid):
                    store.delete_project(pid)
                    await _broadcast_all({"type": "project_list", "projects": store.list_projects()})
                    await _broadcast_all({"type": "conversation_list_changed"})
                continue

            if mtype == "set_conversation_project":
                target_id = msg.get("conversation_id")
                pid = msg.get("project_id")  # None/absent = unassign
                if target_id and store.conversation_exists(target_id) and (pid is None or store.project_exists(pid)):
                    store.set_conversation_project(target_id, pid)
                    if pid:
                        store.touch_project(pid)
                    await _broadcast_all({"type": "conversation_list_changed"})
                    await _broadcast_all({"type": "project_list", "projects": store.list_projects()})
                continue

            if mtype == "list_files":
                target_id = msg.get("conversation_id") or info["conversation_id"]
                await websocket.send_json({
                    "type": "file_list", "conversation_id": target_id,
                    "files": store.list_files(conversation_id=target_id) +
                             ([f for f in store.list_files(project_id=store.get_conversation(target_id)["project_id"])]
                              if store.get_conversation(target_id) and store.get_conversation(target_id).get("project_id") else []),
                })
                continue

            if mtype == "delete_file":
                fid = msg.get("file_id")
                f = store.get_file(fid) if fid else None
                if f:
                    store.delete_file(fid)
                    scope_conv = f.get("conversation_id") or info["conversation_id"]
                    await _broadcast_watchers(scope_conv, {"type": "file_deleted", "file_id": fid})
                continue

            if mtype == "list_tools":
                await websocket.send_json({"type": "tool_list", "tools": _tool_catalog()})
                continue

            if mtype == "list_teams":
                await websocket.send_json({"type": "team_list", "teams": _team_catalog()})
                continue

            # ------------------------------------------------------------ Phase 5: skill review queue --
            # Every mutating action here (approve/reject/disable/edit) is something only a
            # connected device can trigger — same trust model as the rest of this websocket
            # protocol. None of these paths are reachable from reflection.py or any other
            # autonomous code; see skills.py's docstring for why that separation is load-bearing.
            if mtype == "list_skills":
                import skills as _skills
                status_filter = msg.get("status")
                await websocket.send_json({
                    "type": "skill_list",
                    "skills": [_skill_to_dict(s) for s in _skills.list_skills(status=status_filter)],
                })
                continue

            if mtype == "approve_skill":
                import skills as _skills
                skill = _skills.approve_skill(msg.get("name", ""))
                if skill:
                    await _broadcast_all({"type": "skill_updated", "skill": _skill_to_dict(skill)})
                else:
                    await websocket.send_json({"type": "error", "message": f"No such skill: {msg.get('name')!r}"})
                continue

            if mtype == "reject_skill":
                import skills as _skills
                skill = _skills.reject_skill(msg.get("name", ""))
                if skill:
                    await _broadcast_all({"type": "skill_updated", "skill": _skill_to_dict(skill)})
                else:
                    await websocket.send_json({"type": "error", "message": f"No such skill: {msg.get('name')!r}"})
                continue

            if mtype == "disable_skill":
                import skills as _skills
                skill = _skills.disable_skill(msg.get("name", ""))
                if skill:
                    await _broadcast_all({"type": "skill_updated", "skill": _skill_to_dict(skill)})
                else:
                    await websocket.send_json({"type": "error", "message": f"No such skill: {msg.get('name')!r}"})
                continue

            if mtype == "clear_skill_flag":
                import skills as _skills
                skill = _skills.clear_flag(msg.get("name", ""))
                if skill:
                    await _broadcast_all({"type": "skill_updated", "skill": _skill_to_dict(skill)})
                else:
                    await websocket.send_json({"type": "error", "message": f"No such skill: {msg.get('name')!r}"})
                continue

            if mtype == "edit_skill":
                import skills as _skills
                skill = _skills.edit_skill(
                    msg.get("name", ""), when_to_use=msg.get("when_to_use"),
                    steps=msg.get("steps"), tool_calls=msg.get("tools"),
                )
                if skill:
                    await _broadcast_all({"type": "skill_updated", "skill": _skill_to_dict(skill)})
                else:
                    await websocket.send_json({"type": "error", "message": f"No such skill: {msg.get('name')!r}"})
                continue

            if mtype == "new_conversation":
                _detach_from_current(websocket)
                new_id = store.create_conversation(device_id=info["device_id"])
                requested_project = msg.get("project_id")
                if requested_project and store.project_exists(requested_project):
                    store.set_conversation_project(new_id, requested_project)
                    store.touch_project(requested_project)
                connected[websocket]["conversation_id"] = new_id
                watchers.setdefault(new_id, set()).add(websocket)
                await websocket.send_json({
                    "type": "conversation_opened", "conversation_id": new_id,
                    "title": "New conversation", "messages": [],
                    "project_id": requested_project if requested_project and store.project_exists(requested_project) else None,
                })
                continue

            if mtype == "open_conversation":
                target_id = msg.get("conversation_id")
                if not target_id or not store.conversation_exists(target_id):
                    await websocket.send_json({"type": "error", "message": "No such conversation."})
                    continue
                _detach_from_current(websocket)
                connected[websocket]["conversation_id"] = target_id
                watchers.setdefault(target_id, set()).add(websocket)
                conv = store.get_conversation(target_id)
                await websocket.send_json({
                    "type": "conversation_opened", "conversation_id": target_id,
                    "title": conv["title"] if conv else "", "project_id": conv.get("project_id") if conv else None,
                    "messages": store.get_messages(target_id)
                })
                continue

            if mtype == "rename_conversation":
                target_id = msg.get("conversation_id")
                title = msg.get("title", "")
                if target_id and store.conversation_exists(target_id) and title.strip():
                    store.rename_conversation(target_id, title)
                    await _broadcast_all({
                        "type": "conversation_renamed", "conversation_id": target_id,
                        "title": store.get_conversation(target_id)["title"]
                    })
                continue

            if mtype == "delete_conversation":
                target_id = msg.get("conversation_id")
                if target_id and store.conversation_exists(target_id):
                    store.delete_conversation(target_id)
                    for ws2, info2 in list(connected.items()):
                        if info2.get("conversation_id") == target_id:
                            info2["conversation_id"] = None
                    watchers.pop(target_id, None)
                    await _broadcast_all({"type": "conversation_deleted", "conversation_id": target_id})
                continue

            if mtype != "message":
                continue

            text = (msg.get("text") or "").strip()
            if not text:
                continue
            source = msg.get("source", "text")

            conv_id = info["conversation_id"]
            if conv_id is None:  # only possible if this socket's conversation was just deleted
                conv_id = store.create_conversation(device_id=info["device_id"])
                connected[websocket]["conversation_id"] = conv_id
                watchers.setdefault(conv_id, set()).add(websocket)

            user_msg_id = store.add_message(conv_id, "user", text, source=source)
            asyncio.create_task(_embed_message_background(user_msg_id, conv_id, text))
            # Excludes the sender: it already rendered its own message optimistically the
            # moment it hit send — this broadcast is only so OTHER watchers of the same
            # conversation (another tab, the voice companion) see it too.
            await _broadcast_watchers(conv_id, {
                "type": "user_message", "conversation_id": conv_id, "text": text, "source": source
            }, exclude=websocket)

            chat_history = store.get_llm_context_history(conv_id)
            result = await _run_turn(conv_id, chat_history, info["capabilities"])

            assistant_text = (result.get("response") or "").strip()
            if assistant_text:
                assistant_msg_id = store.add_message(conv_id, "assistant", assistant_text, source="text")
                asyncio.create_task(_embed_message_background(assistant_msg_id, conv_id, assistant_text))
                # Phase 8c: pulse the Working Memory region — a new turn just landed in
                # long-term storage, whether or not anyone's currently watching mind.html.
                conv_meta = store.get_conversation(conv_id)
                asyncio.create_task(_broadcast_mind({
                    "type": "memory_pulse", "conversation_id": conv_id,
                    "title": conv_meta.get("title") if conv_meta else None,
                }))

            await _broadcast_watchers(conv_id, {
                "type": "stream_end", "conversation_id": conv_id,
                "full_text": assistant_text,
                "tools_ran": [t.get("tool") for t in result.get("tools_ran", [])],
                "denied": result.get("denied", []),
            })

            asyncio.create_task(_maybe_autotitle(conv_id))
            asyncio.create_task(_maybe_update_summary(conv_id))
            asyncio.create_task(_maybe_reflect(text, result))

    except WebSocketDisconnect:
        pass
    finally:
        _detach(websocket)


def _detach_from_current(websocket):
    info = connected.get(websocket)
    if not info:
        return
    cid = info.get("conversation_id")
    if cid and cid in watchers:
        watchers[cid].discard(websocket)
        if not watchers[cid]:
            del watchers[cid]


# ---------------------------------------------------------------------------
# Living Mind — read-only observer socket (Phase 8c)
# ---------------------------------------------------------------------------
# Deliberately separate from /ws rather than piggybacked onto it: /ws is a conversation
# participant's connection (it can send messages, switch conversations, request approvals);
# /ws/mind only ever receives a snapshot plus live pulse events. Keeping them apart means a
# bug in one protocol can't leak into the other, and a mind.html tab left open forever is
# never mistaken for an idle conversation. Still goes through the same device-token
# handshake and the same auth rate limiter as /ws — tool names and conversation titles
# aren't secrets on the level of a credential, but there's no reason to expose them to an
# unauthenticated LAN connection either when the existing device-auth machinery is right
# there.
@app.websocket("/ws/mind")
async def mind_websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    try:
        hello = await asyncio.wait_for(websocket.receive_json(), timeout=15)
    except Exception:
        await websocket.close(code=4000, reason="Expected a hello handshake within 15s")
        return
    if hello.get("type") != "hello":
        await websocket.close(code=4000, reason="First message must be type=hello")
        return

    device_id = hello.get("device_id", "")
    token = hello.get("token", "")
    client_host = websocket.client.host if websocket.client else "unknown"

    locked_until = _auth_limiter.is_locked(client_host)
    if locked_until:
        wait_s = int(locked_until - time.time())
        await websocket.send_json({"type": "error", "message": f"Too many failed auth attempts. Try again in {wait_s}s."})
        await websocket.close(code=4429, reason="Rate limited")
        return

    if registry.validate(device_id, token) is None:
        _auth_limiter.record_failure(client_host)
        await websocket.send_json({"type": "error", "message": "Unknown device or invalid token."})
        await websocket.close(code=4001, reason="Unauthorized")
        return
    _auth_limiter.record_success(client_host)

    mind_watchers.add(websocket)
    try:
        from mind_graph import build_snapshot
        snapshot = await asyncio.to_thread(build_snapshot)
        await websocket.send_json({"type": "snapshot", **snapshot})

        while True:
            # Nothing meaningful for this client to send us — just keep the connection open
            # and let a disconnect raise, same idle-read pattern the rest of this endpoint
            # family uses. A client-sent "refresh" requests a fresh snapshot on demand
            # (e.g. after the tab was backgrounded a while and might have missed pulses).
            msg = await websocket.receive_json()
            if msg.get("type") == "refresh":
                snapshot = await asyncio.to_thread(build_snapshot)
                await websocket.send_json({"type": "snapshot", **snapshot})
    except WebSocketDisconnect:
        pass
    finally:
        mind_watchers.discard(websocket)


@app.get("/mind")
async def mind_page():
    """Serves webapp/mind.html directly, so the Living Mind view has a clean URL
    (/mind) instead of relying on StaticFiles' extension-based fallback."""
    from fastapi.responses import FileResponse
    path = os.path.join(WEBAPP_DIR, "mind.html")
    if not os.path.exists(path):
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse("mind.html not found.", status_code=404)
    return FileResponse(path)


@app.get("/gui-config")
async def gui_config():
    """Lets the served web page bootstrap its own device credentials on load — see
    _ensure_web_gui_device() for why this is unauthenticated by design."""
    token = _ensure_web_gui_device()
    return {"device_id": WEB_GUI_DEVICE_ID, "token": token}


@app.get("/health")
async def health():
    return {
        "status": "ok", "connected_devices": len(connected),
        "open_conversations": len(watchers),
        "brain_memory_facts": len(shared_memory.semantic_memory.get("facts", [])),
    }


@app.post("/upload")
async def upload_file(file: UploadFile = File(...), conversation_id: str = Form(...)):
    """File/folder attachments. A folder upload from the browser is just many calls to this
    same endpoint, one per file — see webapp/app.js. Scoped to the project if the target
    conversation is in one (shared across every chat in that project), otherwise scoped to
    just this conversation. See file_ingest.py for what "extractable" means."""
    from fastapi.responses import JSONResponse
    from file_ingest import extract_text, MAX_UPLOAD_BYTES

    if not store.conversation_exists(conversation_id):
        return JSONResponse({"error": "No such conversation."}, status_code=404)

    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        return JSONResponse(
            {"error": f"File too large — max {MAX_UPLOAD_BYTES // (1024 * 1024)}MB."}, status_code=413
        )

    filename = file.filename or "upload"
    text, extractable, truncated = extract_text(filename, raw)
    conv = store.get_conversation(conversation_id)
    project_id = conv.get("project_id") if conv else None

    file_id = store.add_file(
        filename=filename, content_text=text, size_bytes=len(raw),
        conversation_id=None if project_id else conversation_id,
        project_id=project_id, extractable=extractable, truncated=truncated,
    )
    file_meta = {
        "id": file_id, "filename": filename, "size_bytes": len(raw),
        "extractable": extractable, "truncated": truncated, "uploaded_at": time.time(),
        "conversation_id": None if project_id else conversation_id, "project_id": project_id,
    }
    await _broadcast_watchers(conversation_id, {"type": "file_uploaded", "file": file_meta})
    return file_meta


@app.post("/transcribe")
async def transcribe(audio: UploadFile = File(...)):
    """Browser push-to-talk: the page captures raw PCM16LE mono @ 16kHz via the Web Audio
    API (see webapp/app.js) and posts the raw bytes here. Reuses voice.py's already-tested
    local Vosk engine — same zero-API-key STT the wake-word companion uses, just fed bytes
    that came from a browser tab's mic instead of this machine's own. The audio is read
    into memory for this one call and never written to disk, matching voice.py's retention
    policy for every other STT path."""
    from fastapi.responses import JSONResponse
    try:
        from voice import voice
    except Exception as e:
        return JSONResponse({"error": f"Voice module unavailable: {e}"}, status_code=503)

    raw = await audio.read()
    if not raw:
        return {"text": ""}
    text = voice._stt_local_bytes(raw)
    return {"text": text or ""}


@app.get("/export/{conversation_id}")
async def export_conversation(conversation_id: str):
    """Downloads a conversation as a plain markdown file — your data, in a format you can
    actually keep, read offline, or move elsewhere. Not part of create_backup's zip (that's
    the whole database); this is one conversation, human-readable, on demand."""
    if not store.conversation_exists(conversation_id):
        return Response(content="No such conversation.", status_code=404)

    conv = store.get_conversation(conversation_id)
    messages = store.get_messages(conversation_id)

    lines = [f"# {conv['title']}", "", f"_Exported {datetime.now().strftime('%Y-%m-%d %H:%M')}_", ""]
    for m in messages:
        speaker = "**Devin**" if m["role"] == "user" else "**Jarvis**"
        via = " _(voice)_" if m.get("source") == "voice" else ""
        lines.append(f"{speaker}{via}:")
        lines.append(m["content"])
        lines.append("")
    markdown = "\n".join(lines)

    safe_title = re.sub(r'[^\w\-]+', '_', conv["title"]).strip("_")[:50] or "conversation"
    filename = f"{safe_title}_{conversation_id[:8]}.md"
    return Response(
        content=markdown, media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


# Mounted last, deliberately — a broad "/" static mount must not shadow the more specific
# routes above (/ws, /health). Only mounts if webapp/ actually exists, so a server-only
# checkout (no frontend built yet) still runs.
if os.path.isdir(WEBAPP_DIR):
    app.mount("/", StaticFiles(directory=WEBAPP_DIR, html=True), name="webapp")


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Jarvis multi-device server")
    parser.add_argument("--register", metavar="DEVICE_ID", help="Register a new device and print its token, then exit")
    parser.add_argument("--name", default="", help="Display name for --register")
    parser.add_argument("--capabilities", default="", help="Comma-separated capabilities to grant, e.g. filesystem,microphone")
    parser.add_argument("--revoke", metavar="DEVICE_ID", help="Revoke a device's access and exit")
    parser.add_argument("--list-devices", action="store_true", help="List registered devices and exit")
    args = parser.parse_args()

    if args.list_devices:
        for device_id, info in registry.list_devices().items():
            print(f"{device_id}: {info['name']} — capabilities={info['allowed_capabilities']}")
        return

    if args.revoke:
        ok = registry.revoke(args.revoke)
        print(f"Revoked '{args.revoke}'." if ok else f"'{args.revoke}' not found.")
        return

    if args.register:
        caps = [c.strip() for c in args.capabilities.split(",") if c.strip()]
        token = registry.register(args.register, name=args.name, capabilities=caps)
        print(f"Registered device '{args.register}' (capabilities: {caps or '[none]'})")
        print(f"Token (copy this — it will not be shown again):\n  {token}")
        return

    import uvicorn
    host = os.getenv("JARVIS_SERVER_HOST", "0.0.0.0")
    port = int(os.getenv("JARVIS_SERVER_PORT", "8765"))
    print(f"Starting Jarvis server on {host}:{port} ...")
    if os.path.isdir(WEBAPP_DIR):
        print(f"Web GUI: http://{host if host != '0.0.0.0' else 'localhost'}:{port}/")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()

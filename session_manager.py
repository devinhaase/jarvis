"""
session_manager.py — The shared ReAct loop ("the brain"), usable by both the CLI (main.py,
standalone) and the multi-device server (server.py). One JarvisBrain instance wraps exactly
one Coordinator + Memory + LLM — the "central brain" the multi-device architecture calls for.
The CLI running standalone creates one for itself; server.py creates exactly one and every
connected device/session talks through it, sharing the same long-term memory.
"""

import re
from coordinator import Coordinator
from tools import ALL_TOOLS, REQUIRES_CAPABILITY


class JarvisBrain:
    def __init__(self, approval_fn=None, memory=None, conversation_store=None):
        """
        approval_fn, memory: passed straight through to Coordinator — see its docstring.
        The multi-device server creates one shared Memory (the "central brain") and one
        JarvisBrain per connected session, each with its own approval_fn closure so a Tier-3/4
        confirmation routes back to whichever device actually triggered it.
        conversation_store: injected the same way, rather than reaching for
        conversation_store.store fresh inside build_prompt() — a module-level singleton
        grabbed via a function-local import can't be swapped out by a caller (e.g. a test
        using an isolated store), it always resolves back to the real one.
        """
        self.coordinator = Coordinator(approval_fn=approval_fn, memory=memory)
        self.memory = self.coordinator.memory  # single source of truth — no second instance
        if conversation_store is None:
            from conversation_store import store as _default_store
            conversation_store = _default_store
        self._store = conversation_store
        self._brain = None
        self._llm_error = None
        try:
            from llm import get_llm
            self._brain = get_llm()
        except Exception as e:
            self._llm_error = str(e)

    def is_available(self) -> bool:
        return self._brain is not None

    def build_prompt(self, conversation_id: str = None) -> str:
        from llm import build_system_prompt
        semantic_ctx = self.memory.get_semantic_context()
        recent_eps = self.memory.get_recent_episodes(n=5)
        files_ctx = ""
        if conversation_id:
            from file_ingest import build_context_block
            files_ctx = build_context_block(self._store.get_context_files(conversation_id))
        return build_system_prompt(semantic_ctx, recent_eps, files_ctx)

    def process_turn(self, chat_history: list, device_capabilities: list = None,
                      max_iterations: int = 3, on_tool_step=None, conversation_id: str = None) -> dict:
        """
        Run one full ReAct turn: LLM -> optional tool execution -> LLM again if tools ran.
        Mutates `chat_history` in place (appends assistant/tool messages) and returns it as
        part of the result, so callers keep one source of truth for the conversation.

        `device_capabilities`: list of capability strings the requesting session's device
        declared (e.g. ["filesystem"]), or None to mean "no restriction" (the CLI's own
        standalone/local mode — there's no separate device to under-trust). Tools tagged in
        tools.REQUIRES_CAPABILITY get refused, not executed, if the session lacks the tag.

        `on_tool_step(tool_name, tier_name)`, if given, fires before each allowed tool runs —
        used by the server to notify a client mid-turn.

        Returns {"response": str, "tools_ran": [...], "denied": [...]}.
        """
        if self._brain is None:
            msg = f"AI Brain unavailable: {self._llm_error}"
            return {"response": msg, "tools_ran": [], "denied": []}

        sys_prompt = self.build_prompt(conversation_id)
        tools_ran = []
        denied = []
        final_response = ""
        iteration = 0

        while iteration < max_iterations:
            iteration += 1
            try:
                response_data = self._brain.chat(chat_history, sys_prompt)
            except Exception as e:
                return {"response": f"AI Brain error: {e}", "tools_ran": tools_ran, "denied": denied}

            raw_response = response_data.get("response", "")
            tools_to_run = response_data.get("tools", [])
            clean_response = self._persist_remember_tags(raw_response) if raw_response else ""

            if tools_to_run:
                allowed, blocked = self._filter_by_capability(tools_to_run, device_capabilities)
                denied.extend(blocked)

                chat_history.append({"role": "assistant", "content": clean_response or "[Running tools...]"})

                if on_tool_step:
                    for step in allowed:
                        on_tool_step(step.get("tool"), self._tier_name(step.get("tool")))

                tool_results = self.coordinator.run_tools(allowed) if allowed else ""
                if blocked:
                    blocked_msg = "\n".join(
                        f"{b['tool']}: refused — this session's device didn't declare the "
                        f"'{b['required']}' capability required for this tool."
                        for b in blocked
                    )
                    tool_results = (tool_results + "\n" + blocked_msg).strip()

                chat_history.append({
                    "role": "user",
                    "content": f"[SYSTEM] Tool execution complete. Results:\n{tool_results}\n\nProvide your final response."
                })
                tools_ran.extend(allowed)
                continue
            else:
                if clean_response:
                    chat_history.append({"role": "assistant", "content": clean_response})
                final_response = clean_response
                break

        return {"response": final_response, "tools_ran": tools_ran, "denied": denied}

    def process_turn_stream(self, chat_history: list, device_capabilities: list = None,
                             max_iterations: int = 3, on_status=None, conversation_id: str = None):
        """
        Generator version of process_turn() for real-time UIs (the web GUI, the voice
        companion's live transcript). Same ReAct loop, same chat_history mutation, same
        capability filtering — the only difference is *when* the caller finds out what
        Jarvis said: here, as it's generated, not after the whole turn finishes.

        Yields ("chunk", text) for each piece of visible text as a round streams in,
        ("round_end", {"tools_ran": [...]}) when a round finishes (so a UI can show a
        divider before tool output vs. the next round's answer), and exactly one final
        ("done", {"response": str, "tools_ran": [...], "denied": [...]}) — same shape
        process_turn() returns, for callers that just want the end result.

        `on_status(event, data)`, if given, fires for out-of-band events a UI might want
        to react to immediately rather than waiting for the next chunk — currently just
        "tools_starting" with the list of tool names about to run.
        """
        from llm import stream_and_filter_tags

        if self._brain is None:
            msg = f"AI Brain unavailable: {self._llm_error}"
            yield ("chunk", msg)
            yield ("done", {"response": msg, "tools_ran": [], "denied": []})
            return

        sys_prompt = self.build_prompt(conversation_id)
        tools_ran = []
        denied = []
        final_response = ""
        iteration = 0

        while iteration < max_iterations:
            iteration += 1
            tool_calls = []

            def _on_tool(m, _sink=tool_calls):
                from llm import _tool_call_from_match
                _sink.append(_tool_call_from_match(m))

            def _on_remember(m):
                self.memory.append_fact(m.group(1))

            try:
                raw_chunks = self._brain.chat_stream(chat_history, sys_prompt)
            except Exception as e:
                err = f"AI Brain error: {e}"
                yield ("chunk", err)
                yield ("done", {"response": err, "tools_ran": tools_ran, "denied": denied})
                return

            collected = []
            try:
                for visible in stream_and_filter_tags(raw_chunks, on_tool=_on_tool, on_remember=_on_remember):
                    if visible:
                        collected.append(visible)
                        yield ("chunk", visible)
            except Exception as e:
                err = f"AI Brain error mid-stream: {e}"
                yield ("chunk", err)
                yield ("done", {"response": err, "tools_ran": tools_ran, "denied": denied})
                return

            clean_response = "".join(collected).strip()

            if tool_calls:
                allowed, blocked = self._filter_by_capability(tool_calls, device_capabilities)
                denied.extend(blocked)

                chat_history.append({"role": "assistant", "content": clean_response or "[Running tools...]"})

                if on_status:
                    on_status("tools_starting", [s.get("tool") for s in allowed])

                tool_results = self.coordinator.run_tools(allowed) if allowed else ""
                if blocked:
                    blocked_msg = "\n".join(
                        f"{b['tool']}: refused — this session's device didn't declare the "
                        f"'{b['required']}' capability required for this tool."
                        for b in blocked
                    )
                    tool_results = (tool_results + "\n" + blocked_msg).strip()

                chat_history.append({
                    "role": "user",
                    "content": f"[SYSTEM] Tool execution complete. Results:\n{tool_results}\n\nProvide your final response."
                })
                tools_ran.extend(allowed)
                yield ("round_end", {"tools_ran": [s.get("tool") for s in allowed]})
                continue
            else:
                if clean_response:
                    chat_history.append({"role": "assistant", "content": clean_response})
                final_response = clean_response
                yield ("round_end", {"tools_ran": []})
                break

        yield ("done", {"response": final_response, "tools_ran": tools_ran, "denied": denied})

    def _tier_name(self, tool_name: str) -> str:
        t = ALL_TOOLS.get(tool_name)
        return t.tier.name if t else "UNKNOWN"

    def _filter_by_capability(self, tools_to_run: list, device_capabilities):
        """Split requested tool calls into (allowed, blocked). None capabilities = no restriction."""
        if device_capabilities is None:
            return tools_to_run, []
        allowed, blocked = [], []
        caps = set(device_capabilities)
        for step in tools_to_run:
            name = step.get("tool")
            required = REQUIRES_CAPABILITY.get(name)
            if required and required not in caps:
                blocked.append({"tool": name, "required": required})
            else:
                allowed.append(step)
        return allowed, blocked

    def _persist_remember_tags(self, response_text: str) -> str:
        from llm import extract_remember_facts
        facts = extract_remember_facts(response_text)
        for fact in facts:
            self.memory.append_fact(fact)
        return re.sub(r'\[REMEMBER:\s*.+?\]', '', response_text).strip()

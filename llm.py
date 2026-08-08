import os
import re
import json
import requests
from dotenv import load_dotenv
from tools import ALL_TOOLS

load_dotenv()

# ---------------------------------------------------------------------------
# PERSONA SPECIFICATION — Phase 5: loaded live from SOUL.md, not hardcoded here.
# ---------------------------------------------------------------------------
# This is not a personality description for an AI assistant.
# It is an operational spec — Jarvis's actual defaults, hard rules, and voice.
#
# SOUL.md is the actual source of truth — edit it directly to change how Jarvis behaves,
# no code change needed. Read fresh on every import (module-load time, same as PERSONA
# always was a module-level constant) rather than cached forever, so a restart always
# picks up an edit — matches this project's existing "reload allowlists from disk, don't
# trust an in-memory copy" convention (auth_check.py, device_registry.py).
#
# _FALLBACK_PERSONA exists only so a missing/corrupted SOUL.md degrades to *something*
# coherent rather than crashing the whole agent at import time — same
# never-crash-on-a-missing-optional-file posture the rest of this codebase already has.
# It is deliberately NOT kept in sync with SOUL.md's exact wording; if you're reading this
# comment because the fallback fired, that's a sign SOUL.md needs restoring, not a bug.
# ---------------------------------------------------------------------------
_SOUL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "SOUL.md")
_FALLBACK_PERSONA = """
IDENTITY
You are Jarvis — Devin's autonomous personal assistant and ops agent. SOUL.md could not be
read, so this is a minimal fallback persona, not the real one. Mention this to Devin.
"""


def _load_persona() -> str:
    try:
        with open(_SOUL_PATH, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return _FALLBACK_PERSONA


PERSONA = _load_persona()

SYSTEM_PROMPT_TEMPLATE = """{persona}
{team_context}
LONG-TERM MEMORY (cross-session context):
{semantic_context}

RECENT ACTIONS (last session summary):
{recent_episodes}

ATTACHED FILES (uploaded to this conversation or its project — reference them naturally
when relevant, the way you'd reference anything else Devin has told you):
{attached_files}

AVAILABLE TOOLS:
{tool_descriptions}

OUTPUT FORMAT
Reply in plain natural language — exactly what Devin sees, streamed to him as you write it.
Do not wrap your answer in JSON, markdown code fences, or any other structure.

If (and only if) you need to run a tool before you can answer, respond with ONLY one or
more lines of this exact form, and nothing else in that message — your real answer comes
in the next turn once tool results are back:
[TOOL: tool_name {{"arg1": "value"}}]
[TOOL: another_tool {{}}]
Use {{}} for a tool that takes no arguments. Args must be valid single-line JSON.

To persist a new long-term fact about Devin, add this anywhere in a normal answer:
[REMEMBER: <one-sentence fact>]

Example — answering directly, no tool needed:
Your CPU's fine, nothing else needed here.

Example — a tool is needed first:
[TOOL: check_system_health {{}}]
"""

def get_tool_descriptions(tool_subset: set = None):
    """Phase 4: `tool_subset`, if given, restricts the listing to just those tool names —
    this is how a team's scoped sub-loop sees only its own tools in the prompt. This alone
    is prompt-level hiding, not enforcement — Coordinator.run_tools()'s own tool_subset
    check is the real gate, same defense-in-depth pattern offense-authorization already
    uses (checked both by the tool and centrally at dispatch)."""
    desc = ""
    for name, tool in ALL_TOOLS.items():
        if tool_subset is not None and name not in tool_subset:
            continue
        desc += f"- {name}: {tool.description}. (Tier {tool.tier.name})\n"
    return desc

def build_system_prompt(semantic_context: str = "", recent_episodes: list = None,
                         attached_files_context: str = "", tool_subset: set = None,
                         team_context: str = "") -> str:
    episodes_str = ""
    if recent_episodes:
        for ep in recent_episodes[-3:]:
            result_preview = ep['result'][:100] if len(ep['result']) > 100 else ep['result']
            episodes_str += f"  - [{ep['tier']}] {ep['action']}: {result_preview}\n"
    if not episodes_str:
        episodes_str = "  None yet."

    return SYSTEM_PROMPT_TEMPLATE.format(
        persona=PERSONA,
        team_context=("\n" + team_context + "\n") if team_context else "",
        semantic_context=semantic_context if semantic_context else "  None stored yet.",
        recent_episodes=episodes_str,
        attached_files=attached_files_context if attached_files_context else "  None attached.",
        tool_descriptions=get_tool_descriptions(tool_subset)
    )

def extract_remember_facts(response_text: str) -> list:
    """Pull any [REMEMBER: ...] tags out of Jarvis's response text. Kept for callers that
    go through chat() (non-streaming) — parse_tagged_response() deliberately leaves
    [REMEMBER: ...] tags untouched in that path so this still finds them downstream, same
    as before this file supported streaming."""
    return re.findall(r'\[REMEMBER:\s*(.+?)\]', response_text, re.DOTALL)

# ---------------------------------------------------------------------------
# TAGGED-OUTPUT PARSING — shared by every provider, streaming or not
# ---------------------------------------------------------------------------
# Tool calls used to be extracted by requiring the whole reply to be one JSON object
# (strict {"response":..., "tools":[...]} mode). That's fine for a single non-streamed
# call, but it means nothing can ever be shown to the user until generation is 100%
# finished — you can't stream half of a JSON string as readable text. Real streaming
# chat needs the reply itself to BE the plain text, with tool calls as inline markers
# that can be recognized and stripped out as they arrive, not wrapped around everything.
# ---------------------------------------------------------------------------

TOOL_TAG_RE = re.compile(r'\[TOOL:\s*(\w+)\s*(\{.*?\})?\s*\]', re.DOTALL)
REMEMBER_TAG_RE = re.compile(r'\[REMEMBER:\s*(.+?)\]', re.DOTALL)
# Phase 5: a team's system prompt (see teams.py's active-skill blurbs) invites the model to
# name which learned skill it followed, if any — stripped from visible output exactly like
# REMEMBER, so a technical tag never leaks into what Devin sees.
SKILL_USED_TAG_RE = re.compile(r'\[SKILL_USED:\s*(.+?)\]', re.DOTALL)


def _tool_call_from_match(m) -> dict:
    name = m.group(1)
    args_str = m.group(2)
    args = {}
    if args_str:
        try:
            args = json.loads(args_str)
        except json.JSONDecodeError:
            args = {}  # malformed args — tool still "runs" with no args rather than
                       # silently dropping the whole line; the tool itself will error
                       # clearly if it actually needed them.
    return {"tool": name, "args": args}


def stream_and_filter_tags(chunks, on_tool=None, on_remember=None, on_skill_used=None):
    """
    Consume a raw text-chunk iterator from a provider and yield only the human-visible
    parts, live, pulling [TOOL: ...] / [REMEMBER: ...] / [SKILL_USED: ...] tags out as each
    one completes and firing the matching callback instead of ever yielding their literal
    characters.

    on_tool(match) / on_remember(match) / on_skill_used(match) fire with the raw re.Match —
    pass None for a tag type you want left untouched in the visible output (see
    parse_tagged_response(), which leaves REMEMBER tags in place for the existing
    downstream extraction to handle).

    Known simplification: a tag is considered "closed" at the first ']' after its '['.
    A tool argument value containing a literal ']' character would break this. Tool args
    in this app are simple strings (hostnames, filenames, search terms) — an acceptable,
    deliberate trade-off rather than writing a full JSON-aware scanner for a rare case.
    """
    buf = ""
    for delta in chunks:
        if not delta:
            continue
        buf += delta
        while True:
            start = buf.find('[')
            if start == -1:
                if buf:
                    yield buf
                    buf = ""
                break
            if start > 0:
                yield buf[:start]
                buf = buf[start:]
            end = buf.find(']')
            if end == -1:
                break  # tag (or stray '[') not closed yet — wait for more input
            candidate = buf[:end + 1]
            m_tool = TOOL_TAG_RE.fullmatch(candidate)
            m_rem = REMEMBER_TAG_RE.fullmatch(candidate)
            m_skill = SKILL_USED_TAG_RE.fullmatch(candidate)
            if m_tool and on_tool:
                on_tool(m_tool)
                buf = buf[end + 1:]
                continue
            if m_rem and on_remember:
                on_remember(m_rem)
                buf = buf[end + 1:]
                continue
            if m_skill and on_skill_used:
                on_skill_used(m_skill)
                buf = buf[end + 1:]
                continue
            # bracketed text that isn't a recognized tag (e.g. "[1]" in normal prose) —
            # show it exactly as written
            yield candidate
            buf = buf[end + 1:]
    if buf:
        yield buf


def parse_tagged_response(text: str) -> dict:
    """Non-streaming convenience: parse one fully-formed reply string into
    {"response": <display text>, "tools": [...]}. [TOOL: ...] tags are always extracted
    and stripped. [REMEMBER: ...] tags are deliberately left in place in the returned
    text — extract_remember_facts() / callers' _persist_remember_tags() (unchanged)
    handle those downstream, exactly as before streaming existed."""
    tools = []
    visible = "".join(stream_and_filter_tags(
        [text],
        on_tool=lambda m: tools.append(_tool_call_from_match(m)),
        on_remember=None,
    ))
    return {"response": visible.strip(), "tools": tools}


# ---------------------------------------------------------------------------
# LLM PROVIDERS
# ---------------------------------------------------------------------------

class LLMProvider:
    def chat_stream(self, messages: list, system_prompt: str):
        """Yield raw plain-text chunks as the model generates them. Must be implemented
        by subclasses — everything else (chat(), tag parsing) is built on top of this."""
        raise NotImplementedError()

    def chat(self, messages: list, system_prompt: str) -> dict:
        """Non-streaming convenience — joins chat_stream() and parses tags. Existing
        callers (main.py's local chat loop, session_manager.process_turn) use this
        unchanged; only the new streaming GUI path calls chat_stream() directly."""
        full_text = "".join(self.chat_stream(messages, system_prompt))
        return parse_tagged_response(full_text)


class OpenAILlm(LLMProvider):
    def chat_stream(self, messages: list, system_prompt: str):
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        full_messages = [{"role": "system", "content": system_prompt}] + messages
        stream = client.chat.completions.create(
            model="gpt-4o",
            messages=full_messages,
            stream=True,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta


class GeminiLlm(LLMProvider):
    def chat_stream(self, messages: list, system_prompt: str):
        import google.generativeai as genai
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        model = genai.GenerativeModel('gemini-1.5-flash', system_instruction=system_prompt)
        gemini_messages = []
        for msg in messages:
            role = "user" if msg["role"] in ["user", "system"] else "model"
            gemini_messages.append({"role": role, "parts": [msg["content"]]})
        response = model.generate_content(gemini_messages, stream=True)
        for chunk in response:
            if chunk.text:
                yield chunk.text


class AnthropicLlm(LLMProvider):
    def chat_stream(self, messages: list, system_prompt: str):
        from anthropic import Anthropic
        client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        anthropic_messages = []
        for msg in messages:
            role = "user" if msg["role"] in ["user", "system"] else "assistant"
            anthropic_messages.append({"role": role, "content": msg["content"]})
        with client.messages.stream(
            model="claude-3-5-sonnet-20240620",
            max_tokens=1500,
            system=system_prompt,
            messages=anthropic_messages,
        ) as stream:
            for text in stream.text_stream:
                yield text


class OllamaLlm(LLMProvider):
    def chat_stream(self, messages: list, system_prompt: str):
        url = f"{os.getenv('OLLAMA_URL', 'http://localhost:11434')}/api/chat"
        full_messages = [{"role": "system", "content": system_prompt}] + messages
        payload = {
            "model": os.getenv("OLLAMA_MODEL", "llama3"),
            "messages": full_messages,
            "stream": True,
        }
        with requests.post(url, json=payload, stream=True, timeout=120) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                data = json.loads(line)
                content = data.get("message", {}).get("content", "")
                if content:
                    yield content
                if data.get("done"):
                    break


class FallbackLLM(LLMProvider):
    """Wraps a primary provider; if it fails before producing ANY output — connection
    refused, timeout, DNS failure, the local Ollama daemon not running — retries the same
    call against a secondary provider instead of just surfacing the error. Only activates
    if ACTIVE_LLM_FALLBACK is actually configured; otherwise behaves identically to the
    primary alone.

    Deliberately does NOT try to switch mid-stream: if the primary already yielded some
    text and then broke, that's a different, weirder failure mode than "couldn't reach it
    at all" — silently splicing a second provider's continuation onto a partial answer
    would be more confusing than just letting that error surface normally.
    """
    def __init__(self, primary: LLMProvider, fallback: LLMProvider = None, fallback_name: str = None):
        self._primary = primary
        self._fallback = fallback
        self._fallback_name = fallback_name

    def chat_stream(self, messages: list, system_prompt: str):
        if self._fallback is None:
            yield from self._primary.chat_stream(messages, system_prompt)
            return
        try:
            gen = self._primary.chat_stream(messages, system_prompt)
            first_chunk = next(gen)
        except StopIteration:
            return  # primary genuinely produced zero output — not a connection failure
        except Exception as e:
            print(f"[LLM] Primary provider failed before producing output ({e}); "
                  f"falling back to {self._fallback_name}.")
            yield from self._fallback.chat_stream(messages, system_prompt)
            return
        yield first_chunk
        yield from gen


def _build_provider(name: str) -> LLMProvider:
    name = (name or "").lower()
    if name == "openai":
        return OpenAILlm()
    elif name == "gemini":
        return GeminiLlm()
    elif name == "anthropic":
        return AnthropicLlm()
    elif name == "ollama":
        return OllamaLlm()
    else:
        return OpenAILlm()


def get_llm() -> LLMProvider:
    primary = _build_provider(os.getenv("ACTIVE_LLM", "openai"))

    fallback_name = os.getenv("ACTIVE_LLM_FALLBACK", "").strip().lower()
    active_name = os.getenv("ACTIVE_LLM", "openai").strip().lower()
    if fallback_name and fallback_name != active_name:
        fallback = _build_provider(fallback_name)
        return FallbackLLM(primary, fallback, fallback_name)
    return primary

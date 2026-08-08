"""
Tests for Phase 3d: voice_companion.py — the local wake-word client that talks to
server.py over the same protocol as the web GUI.

Doesn't require real mic/wake-word hardware for most of this: _resolve_server_url() is pure,
and _send_and_speak()'s event-draining logic (the part that decides what to do with each
message type coming back from the server, including a Tier-4 approval prompt mid-turn) is
tested against a FakeWebSocket with a scripted queue of canned server messages, and a
FakeVoice that records what it was asked to speak instead of touching real audio hardware.

Run: python test_voice_companion_phase3d.py
"""

import sys
import json
import asyncio
import unittest.mock as mock

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


import voice_companion as vc

# ---------------------------------------------------------------------------
section("1. _resolve_server_url")
# ---------------------------------------------------------------------------
check("no explicit server, defaults to localhost + JARVIS_SERVER_PORT",
      vc._resolve_server_url(None) in ("ws://localhost:8765/ws",),  # default port in this repo's .env
      f"got: {vc._resolve_server_url(None)}")
check("HOST:PORT gets ws:// and /ws added",
      vc._resolve_server_url("192.168.1.5:9000") == "ws://192.168.1.5:9000/ws")
check("an already-full ws:// URL is left alone",
      vc._resolve_server_url("ws://example:1/ws") == "ws://example:1/ws")
check("a wss:// URL is left alone too",
      vc._resolve_server_url("wss://example:1/ws") == "wss://example:1/ws")


# ---------------------------------------------------------------------------
section("2. _send_and_speak — drains events, speaks only the final text")
# ---------------------------------------------------------------------------

class FakeWebSocket:
    def __init__(self, canned_messages):
        self._queue = [json.dumps(m) for m in canned_messages]
        self.sent = []

    async def send(self, text):
        self.sent.append(json.loads(text))

    async def recv(self):
        return self._queue.pop(0)


class FakeVoice:
    def __init__(self):
        self.spoken = []

    def speak(self, text):
        self.spoken.append(text)


async def _run_send_and_speak(messages):
    ws = FakeWebSocket(messages)
    voice = FakeVoice()
    await vc._send_and_speak(ws, voice, "what's my cpu usage")
    return ws, voice


ws, voice = asyncio.run(_run_send_and_speak([
    {"type": "user_message", "text": "what's my cpu usage"},
    {"type": "stream_chunk", "text": "Your "},
    {"type": "stream_chunk", "text": "CPU's fine."},
    {"type": "round_end", "tools_ran": []},
    {"type": "stream_end", "full_text": "Your CPU's fine.", "denied": []},
]))
check("sent exactly one message (the outgoing turn)", len(ws.sent) == 1, f"got: {ws.sent}")
check("outgoing message has source=voice", ws.sent[0].get("source") == "voice", f"got: {ws.sent[0]}")
check("speaks the final stream_end text, not the interim chunks", voice.spoken == ["Your CPU's fine."],
      f"got: {voice.spoken}")


# ---------------------------------------------------------------------------
section("3. _send_and_speak — Tier-4 approval mid-turn gets handled before stream_end")
# ---------------------------------------------------------------------------

with mock.patch("voice_companion.Confirm") as MockConfirm:
    MockConfirm.ask.return_value = True
    ws, voice = asyncio.run(_run_send_and_speak([
        {"type": "approval_request", "approval_id": "abc123", "action": "run_local_script", "tier": "TIER_4"},
        {"type": "stream_chunk", "text": "Done."},
        {"type": "stream_end", "full_text": "Done.", "denied": []},
    ]))
    check("TIER_4 approval prompts the console via Confirm.ask", MockConfirm.ask.called)
    check("an approve reply was sent back with the right approval_id",
          any(m.get("type") == "approve" and m.get("approval_id") == "abc123" and m.get("approved") is True
              for m in ws.sent),
          f"got: {ws.sent}")
    check("still speaks the eventual final answer", voice.spoken == ["Done."])

with mock.patch("voice_companion.Confirm") as MockConfirm:
    ws, voice = asyncio.run(_run_send_and_speak([
        {"type": "approval_request", "approval_id": "xyz", "action": "scan_ports", "tier": "TIER_3"},
        {"type": "stream_end", "full_text": "Scanned.", "denied": []},
    ]))
    check("TIER_3 auto-approves without prompting Confirm.ask", not MockConfirm.ask.called)
    check("TIER_3 still sends an approve=True", any(m.get("approved") is True for m in ws.sent if m.get("type") == "approve"))


# ---------------------------------------------------------------------------
section("4. _send_and_speak — denied tools reported, empty final text doesn't call speak")
# ---------------------------------------------------------------------------
ws, voice = asyncio.run(_run_send_and_speak([
    {"type": "stream_end", "full_text": "", "denied": [{"tool": "run_local_script", "required": "filesystem"}]},
]))
check("empty final text never calls speak()", voice.spoken == [], f"got: {voice.spoken}")


# ---------------------------------------------------------------------------
section("5. run_companion — gracefully refuses to start without an active wake-word pipeline")
# ---------------------------------------------------------------------------

class InactiveVoice:
    def is_active(self): return False
    def has_wake_word(self): return False

with mock.patch.dict(sys.modules, {}):
    import voice as voice_module
    original_voice = voice_module.voice
    voice_module.voice = InactiveVoice()
    try:
        # run_companion should return early (no connection attempt) rather than raising.
        asyncio.run(vc.run_companion("ws://localhost:8765/ws"))
        check("run_companion returns cleanly when voice pipeline is inactive", True)
    except Exception as e:
        check("run_companion returns cleanly when voice pipeline is inactive", False, f"raised: {e}")
    finally:
        voice_module.voice = original_voice


print(f"\n{'=' * 55}\n{PASS} passed, {FAIL} failed\n{'=' * 55}")
sys.exit(1 if FAIL else 0)

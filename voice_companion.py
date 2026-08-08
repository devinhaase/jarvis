"""
voice_companion.py — Local wake-word listener that talks to server.py exactly like any
other client (the web GUI, main.py --server). Nothing about the actual voice pipeline is
new here: this reuses voice.py's tested openWakeWord + Vosk + pyttsx3 stack byte-for-byte
— audio never leaves this machine until the wake word fires and a command is transcribed
locally, at which point only the resulting TEXT goes out, over the same /ws protocol every
other client speaks. What's new is that the turn happens IN a real conversation (persisted,
searchable, visible in the web GUI's sidebar) instead of a standalone loop nobody else can
see — a browser tab watching the same conversation sees the transcribed text and the
streamed reply arrive live, and the reply is spoken here AND appears there as a normal
bubble, so voice and typed history really do live in one place.

Usage:
    python voice_companion.py                     # server at ws://localhost:<JARVIS_SERVER_PORT>/ws
    python voice_companion.py --server 192.168.1.183:8765

Requires (same as `main.py --voice`):
  - requirements-voice.txt installed
  - VOICE_ENABLED=true and a wake word configured in .env (see voice.py)
  - A registered device with the 'microphone' capability — JARVIS_VOICE_DEVICE_ID /
    JARVIS_VOICE_DEVICE_TOKEN if you want a distinct device, otherwise this falls back to
    JARVIS_DEVICE_ID / JARVIS_DEVICE_TOKEN (the same credentials main.py --server uses —
    nothing stops one device's token being used by two concurrent connections).

Known limitation, shared with main.py --server's CLI thin client: a Tier-3/4 approval
triggered by some OTHER device only gets seen here the next time this process is between a
send and its matching stream_end — while blocked in wait_for_wake_word()/listen_command()
waiting on the mic, an incoming approval_request just waits in the socket's receive buffer.
The web GUI doesn't have this gap (its message handler is always live), so it's the more
reliable device to have open for anything Tier-4. Not a regression — the Phase 2d CLI client
had the same property before any of this existed.
"""

import os
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import json
import asyncio
import argparse

from dotenv import load_dotenv
load_dotenv()

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm

console = Console()


def _resolve_server_url(explicit: str = None) -> str:
    if explicit:
        return explicit if explicit.startswith(("ws://", "wss://")) else f"ws://{explicit}/ws"
    port = os.getenv("JARVIS_SERVER_PORT", "8765")
    return f"ws://localhost:{port}/ws"


async def _handle_approval(ws, msg):
    tier = msg.get("tier")
    console.print(f"\n[bold yellow][{tier}][/bold yellow] Server wants to run '[bold]{msg.get('action')}[/bold]'.")
    if tier == "TIER_4":
        approved = Confirm.ask("Approve execution?")
    else:
        console.print("[dim](Tier 3 — proceeding automatically; approving)[/dim]")
        approved = True
    await ws.send(json.dumps({"type": "approve", "approval_id": msg.get("approval_id"), "approved": approved}))


async def _send_and_speak(ws, voice, text: str):
    await ws.send(json.dumps({"type": "message", "text": text, "source": "voice"}))
    while True:
        data = json.loads(await ws.recv())
        mtype = data.get("type")
        if mtype == "approval_request":
            await _handle_approval(ws, data)
            continue
        if mtype in ("stream_chunk", "round_end", "user_message", "conversation_renamed", "voice_status"):
            continue
        if mtype == "stream_end":
            full_text = (data.get("full_text") or "").strip()
            if full_text:
                console.print(f"[bold cyan]Jarvis:[/bold cyan] {full_text}")
                voice.speak(full_text)
            for d in data.get("denied", []):
                console.print(f"[dim](refused: {d['tool']} needs '{d['required']}' capability on the server)[/dim]")
            return


async def run_companion(server_url: str):
    from voice import voice

    if not voice.is_active() or not voice.has_wake_word():
        console.print("[bold red]Voice companion requires the local wake-word pipeline to be active.[/bold red]")
        console.print("Check VOICE_ENABLED=true and a configured wake word in .env, and that "
                       "requirements-voice.txt is installed (see that file for the one-time setup).")
        return

    device_id = os.getenv("JARVIS_VOICE_DEVICE_ID") or os.getenv("JARVIS_DEVICE_ID", "")
    token = os.getenv("JARVIS_VOICE_DEVICE_TOKEN") or os.getenv("JARVIS_DEVICE_TOKEN", "")
    if not device_id or not token:
        console.print(
            "[bold red]No device credentials configured.[/bold red]\n"
            "On the machine running the server, run:\n"
            '  python server.py --register voice-companion --name "Voice Companion" --capabilities microphone\n'
            "Then set JARVIS_VOICE_DEVICE_ID and JARVIS_VOICE_DEVICE_TOKEN in this device's .env\n"
            "(or reuse an existing device's JARVIS_DEVICE_ID / JARVIS_DEVICE_TOKEN)."
        )
        return

    conv_file = os.path.join("data", f".voice_conversation_id_{device_id}")
    conversation_id = None
    if os.path.exists(conv_file):
        with open(conv_file, 'r') as f:
            conversation_id = f.read().strip() or None

    import websockets
    try:
        async with websockets.connect(server_url, max_size=None) as ws:
            await ws.send(json.dumps({
                "type": "hello", "device_id": device_id, "token": token,
                "capabilities": ["microphone"], "conversation_id": conversation_id,
            }))
            ready = json.loads(await ws.recv())
            if ready.get("type") == "error":
                console.print(f"[bold red]Server rejected connection:[/bold red] {ready.get('message')}")
                return

            conversation_id = ready["conversation_id"]
            os.makedirs("data", exist_ok=True)
            with open(conv_file, 'w') as f:
                f.write(conversation_id)

            console.print(Panel(
                f"[bold cyan]Jarvis Voice Companion[/bold cyan]\n"
                f"[dim]conversation {conversation_id[:8]}... | say the wake word to talk | Ctrl+C to stop[/dim]",
                border_style="cyan"
            ))

            while True:
                try:
                    triggered = await asyncio.to_thread(voice.wait_for_wake_word)
                except KeyboardInterrupt:
                    break
                if not triggered:
                    continue

                await ws.send(json.dumps({"type": "voice_status", "state": "listening"}))
                text = await asyncio.to_thread(voice.listen_command)
                await ws.send(json.dumps({"type": "voice_status", "state": "idle"}))

                if not text:
                    console.print('[dim](didn\'t catch that — say the wake word again)[/dim]')
                    continue

                console.print(f"[bold green]You (voice):[/bold green] {text}")
                await _send_and_speak(ws, voice, text)

    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    except Exception as e:
        console.print(f"[bold red]Connection error:[/bold red] {e}")


def main():
    parser = argparse.ArgumentParser(description="Jarvis local voice companion")
    parser.add_argument("--server", metavar="HOST:PORT", help="Server address (default: localhost, JARVIS_SERVER_PORT from .env)")
    args = parser.parse_args()
    server_url = _resolve_server_url(args.server)
    try:
        asyncio.run(run_companion(server_url))
    except KeyboardInterrupt:
        console.print("\n[dim]Voice companion stopped.[/dim]")


if __name__ == "__main__":
    main()

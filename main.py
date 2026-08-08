import argparse
import os
import sys

# See server.py for why: a legacy-codepage console must never crash real work over an emoji
# in a log line. Cheap insurance here even though interactive terminals are more likely to
# already be UTF-8-capable than a headless server process.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import json

# Load .env before anything else imports — several modules (voice.py in particular) read
# env vars at import time, and previously only got them if llm.py happened to have already
# run load_dotenv() first. That's an ordering accident, not a guarantee: any --voice run
# where chat mode hadn't started yet saw VOICE_ENABLED as unset regardless of .env.
from dotenv import load_dotenv
load_dotenv()

from coordinator import Coordinator
from memory import Memory
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich.text import Text
from rich.rule import Rule

console = Console()
memory = Memory()

def print_banner():
    banner = r"""
      _  __      ______   _____ 
     | |/  \    |  _ \ \ / /_ _|
  _  | / /\ \   | |_) \ V / | | 
 | |_| / ____ \ |  _ < > <  | | 
  \___/_/    \_\|_| \_/_/ \_\___|
    Personal Automation Engine
    """
    console.print(Panel(Text(banner, style="bold cyan", justify="center"), border_style="blue"))

def display_menu():
    table = Table(show_header=False, box=None)
    table.add_column("Category", style="bold magenta", width=20)
    table.add_column("Option", style="bold cyan")
    table.add_column("Description", style="white")

    table.add_row("[dim]── Assistant ──[/dim]", "", "")
    table.add_row("", "1", "Check unread emails")
    table.add_row("[dim]── Ops Tasks ──[/dim]", "", "")
    table.add_row("", "2", "Check system health")
    table.add_row("", "3", "Scan Windows Event Logs")
    table.add_row("", "4", "Run security ping (Tier 3)")
    table.add_row("[dim]── Security ──[/dim]", "", "")
    table.add_row("", "5", "Security posture check")
    table.add_row("[dim]── AI Chat ──[/dim]", "", "")
    table.add_row("", "6", "[bold cyan]Chat with Jarvis[/bold cyan]")
    table.add_row("[dim]── Memory ──[/dim]", "", "")
    table.add_row("", "7", "View recent Action Logs")
    table.add_row("[dim]── System ──[/dim]", "", "")
    table.add_row("", "8", "Exit")

    console.print(Panel(table, title="[bold]Welcome, Devin.[/bold]", border_style="magenta"))

def _build_brain():
    """Load LLM with full persona + memory context. Returns (brain, system_prompt)."""
    from llm import get_llm, build_system_prompt, extract_remember_facts
    brain = get_llm()
    semantic_ctx = memory.get_semantic_context()
    recent_eps = memory.get_recent_episodes(n=5)
    sys_prompt = build_system_prompt(semantic_ctx, recent_eps)
    return brain, sys_prompt, extract_remember_facts

def _persist_remember_tags(response_text: str, extract_fn):
    """Extract any [REMEMBER: ...] tags from Jarvis's response and persist them."""
    facts = extract_fn(response_text)
    for fact in facts:
        memory.append_fact(fact)
    # Strip tags from display text
    import re
    return re.sub(r'\[REMEMBER:\s*.+?\]', '', response_text).strip()

def run_chat_mode(c: Coordinator, voice_layer=None):
    """Interactive conversational chatbot loop with ReAct + persona + memory."""
    try:
        brain, sys_prompt, extract_fn = _build_brain()
    except Exception as e:
        console.print(f"[bold red]Failed to load AI Brain:[/bold red] {e}")
        console.print("Make sure your [bold].env[/bold] file is set up with your API key.")
        return

    voice_active = voice_layer and voice_layer.is_active()
    wake_mode = voice_active and voice_layer.has_wake_word()

    console.print()
    if wake_mode:
        voice_hint = '  [dim]Voice active — say "Jarvis" to talk, or Ctrl+C to type instead[/dim]'
    elif voice_active:
        voice_hint = "  [dim]Voice active — speak after prompt[/dim]"
    else:
        voice_hint = ""
    console.print(Panel(
        f"[bold cyan]Jarvis Chat[/bold cyan]{voice_hint}\n"
        "[dim]Type [bold]exit[/bold] or [bold]quit[/bold] to leave.[/dim]",
        border_style="cyan"
    ))
    console.print()

    chat_history = []

    while True:
        # Get input — wake word, push-to-talk, or text
        if wake_mode:
            try:
                triggered = voice_layer.wait_for_wake_word()
            except KeyboardInterrupt:
                triggered = False

            if triggered:
                user_input = voice_layer.listen_command()
                if user_input:
                    console.print(f"[bold green]You[/bold green] [dim](voice)[/dim] {user_input}")
                else:
                    console.print("[dim](didn't catch that — say \"Jarvis\" again, or Ctrl+C to type)[/dim]")
                    continue
            else:
                try:
                    user_input = Prompt.ask("[bold green]You[/bold green]")
                except (KeyboardInterrupt, EOFError):
                    console.print("\n[dim]Exiting chat...[/dim]")
                    break
        elif voice_active:
            console.print("[bold green]You[/bold green] [dim](listening...)[/dim]", end=" ")
            user_input = voice_layer.listen_once()
            if user_input:
                console.print(f"[dim]{user_input}[/dim]")
            else:
                user_input = Prompt.ask("[bold green]You[/bold green]")
        else:
            try:
                user_input = Prompt.ask("[bold green]You[/bold green]")
            except (KeyboardInterrupt, EOFError):
                console.print("\n[dim]Exiting chat...[/dim]")
                break

        if not user_input or not user_input.strip():
            continue

        if user_input.strip().lower() in ("exit", "quit", "bye", "goodbye"):
            farewell = "Goodbye, Devin."
            console.print(f"[bold cyan]Jarvis:[/bold cyan] {farewell}")
            if voice_active:
                voice_layer.speak(farewell)
            break

        chat_history.append({"role": "user", "content": user_input})

        # --- ReAct Loop ---
        max_iterations = 3
        iteration = 0

        while iteration < max_iterations:
            iteration += 1

            with console.status("[bold cyan]🧠 Thinking...[/bold cyan]", spinner="dots"):
                try:
                    response_data = brain.chat(chat_history, sys_prompt)
                except Exception as e:
                    console.print(f"[bold red]AI Brain Error:[/bold red] {e}")
                    break

            jarvis_response = response_data.get("response", "")
            tools_to_run = response_data.get("tools", [])

            # Persist any [REMEMBER] facts and clean display text
            if jarvis_response:
                clean_response = _persist_remember_tags(jarvis_response, extract_fn)
            else:
                clean_response = ""

            if clean_response:
                console.print()
                console.print(f"[bold cyan]Jarvis:[/bold cyan] {clean_response}")
                if voice_active:
                    voice_layer.speak(clean_response)

            if tools_to_run:
                console.print()
                console.print(Rule("[dim]Running Tools[/dim]", style="dim"))
                tool_results = c.run_tools(tools_to_run)
                console.print(Rule(style="dim"))

                chat_history.append({"role": "assistant", "content": clean_response or "[Running tools...]"})
                chat_history.append({
                    "role": "user",
                    "content": f"[SYSTEM] Tool execution complete. Results:\n{tool_results}\n\nProvide your final response."
                })
            else:
                if clean_response:
                    chat_history.append({"role": "assistant", "content": clean_response})
                break

        console.print()

async def _remote_chat_session(server_url: str, voice_layer=None):
    """Thin-client chat over a WebSocket to server.py — same UX as local chat mode, but every
    turn (memory, tool execution, Tier-3/4 approval) happens on whichever machine is running
    the server, not here. This is the whole point of the multi-device architecture: adding a
    new device later means writing a client that speaks this same handshake, not rebuilding
    the brain."""
    import asyncio
    import json as _json
    import websockets

    device_id = os.getenv("JARVIS_DEVICE_ID", "")
    token = os.getenv("JARVIS_DEVICE_TOKEN", "")
    if not device_id or not token:
        console.print(
            "[bold red]No device credentials configured.[/bold red]\n"
            "On the machine running the server, run:\n"
            '  python server.py --register <device-id> --name "..." --capabilities filesystem,microphone\n'
            "Then set JARVIS_DEVICE_ID and JARVIS_DEVICE_TOKEN in this device's .env to the values it prints."
        )
        return

    conversation_id_file = os.path.join("data", f".conversation_id_{device_id}")
    conversation_id = None
    if os.path.exists(conversation_id_file):
        with open(conversation_id_file, 'r') as f:
            conversation_id = f.read().strip() or None

    voice_active = voice_layer and voice_layer.is_active()
    wake_mode = voice_active and voice_layer.has_wake_word()

    try:
        async with websockets.connect(server_url, max_size=None) as ws:
            await ws.send(_json.dumps({
                "type": "hello", "device_id": device_id, "token": token,
                "capabilities": ["filesystem", "microphone"],
                "conversation_id": conversation_id,
            }))
            ready = _json.loads(await ws.recv())
            if ready.get("type") == "error":
                console.print(f"[bold red]Server rejected connection:[/bold red] {ready.get('message')}")
                return

            conversation_id = ready.get("conversation_id")
            os.makedirs("data", exist_ok=True)
            with open(conversation_id_file, 'w') as f:
                f.write(conversation_id)

            console.print(Panel(
                f"[bold cyan]Jarvis (remote)[/bold cyan]  "
                f"[dim]conversation {conversation_id[:8]}... | capabilities: {ready.get('capabilities')} | "
                f"resumed {ready.get('resumed_turns')} prior turns[/dim]\n"
                "[dim]Type [bold]exit[/bold] or [bold]quit[/bold] to leave.[/dim]",
                border_style="cyan"
            ))
            console.print()

            async def handle_approval(msg):
                tier = msg.get("tier")
                console.print(f"\n[bold yellow][{tier}][/bold yellow] Server wants to run '[bold]{msg.get('action')}[/bold]'.")
                if tier == "TIER_4":
                    approved = Confirm.ask("Approve execution?")
                else:
                    console.print("[dim](Tier 3 — proceeding automatically; approving)[/dim]")
                    approved = True
                await ws.send(_json.dumps({"type": "approve", "approval_id": msg.get("approval_id"), "approved": approved}))

            while True:
                if wake_mode:
                    try:
                        triggered = voice_layer.wait_for_wake_word()
                    except KeyboardInterrupt:
                        triggered = False
                    if triggered:
                        user_input = voice_layer.listen_command()
                        if not user_input:
                            console.print('[dim](didn\'t catch that — say "Jarvis" again, or Ctrl+C to type)[/dim]')
                            continue
                        console.print(f"[bold green]You[/bold green] [dim](voice)[/dim] {user_input}")
                    else:
                        try:
                            user_input = Prompt.ask("[bold green]You[/bold green]")
                        except (KeyboardInterrupt, EOFError):
                            break
                elif voice_active:
                    console.print("[bold green]You[/bold green] [dim](listening...)[/dim]", end=" ")
                    user_input = voice_layer.listen_once()
                    if user_input:
                        console.print(f"[dim]{user_input}[/dim]")
                    else:
                        user_input = Prompt.ask("[bold green]You[/bold green]")
                else:
                    try:
                        user_input = Prompt.ask("[bold green]You[/bold green]")
                    except (KeyboardInterrupt, EOFError):
                        break

                if not user_input or not user_input.strip():
                    continue
                if user_input.strip().lower() in ("exit", "quit", "bye", "goodbye"):
                    console.print("[bold cyan]Jarvis:[/bold cyan] Goodbye, Devin.")
                    if voice_active:
                        voice_layer.speak("Goodbye, Devin.")
                    break

                await ws.send(_json.dumps({"type": "message", "text": user_input, "source": "text"}))

                streaming = False
                while True:
                    data = _json.loads(await ws.recv())
                    mtype = data.get("type")
                    if mtype == "approval_request":
                        await handle_approval(data)
                        continue
                    if mtype in ("user_message", "conversation_renamed", "voice_status"):
                        continue
                    if mtype == "tool_status" and data.get("event") == "tools_starting":
                        tools = ", ".join(data.get("data") or [])
                        console.print(f"[dim](running {tools}...)[/dim]")
                        continue
                    if mtype == "round_end":
                        continue
                    if mtype == "stream_chunk":
                        text = data.get("text", "")
                        if text:
                            if not streaming:
                                console.print()
                                console.print("[bold cyan]Jarvis:[/bold cyan] ", end="")
                                streaming = True
                            console.print(text, end="")
                        continue
                    if mtype == "stream_end":
                        full_text = data.get("full_text", "")
                        if streaming:
                            console.print()
                        elif full_text:
                            console.print()
                            console.print(f"[bold cyan]Jarvis:[/bold cyan] {full_text}")
                        if voice_active and full_text:
                            voice_layer.speak(full_text)
                        for d in data.get("denied", []):
                            console.print(f"[dim](refused: {d['tool']} needs '{d['required']}' capability on the server)[/dim]")
                        console.print()
                        break
    except Exception as e:
        console.print(f"[bold red]Connection error:[/bold red] {e}")


def run_remote_chat_mode(server_url: str, voice_layer=None):
    import asyncio
    asyncio.run(_remote_chat_session(server_url, voice_layer))


def main():
    parser = argparse.ArgumentParser(description="Jarvis Automation CLI")
    parser.add_argument("command", nargs="?", help="Direct command or natural language")
    parser.add_argument("--voice", action="store_true", help="Enable voice mode (requires voice deps)")
    parser.add_argument("--server", metavar="HOST:PORT", help="Connect to a Jarvis server as a thin client instead of running locally")
    args = parser.parse_args()

    # Optional voice layer — loads silently, no-ops if disabled
    voice_layer = None
    if args.voice:
        try:
            from voice import voice
            voice_layer = voice
        except Exception:
            console.print("[bold yellow]Voice module unavailable. Continuing in text mode.[/bold yellow]")

    if args.server:
        # Thin-client mode: no local Coordinator at all — every turn runs on the server.
        server_url = args.server
        if not server_url.startswith(("ws://", "wss://")):
            server_url = f"ws://{server_url}/ws"
        run_remote_chat_mode(server_url, voice_layer)
        return

    c = Coordinator()

    if args.command:
        if args.command == 'emails':
            c.run_tools([{"tool": "read_recent_emails"}])
        elif args.command == 'health':
            c.run_tools([{"tool": "check_system_health"}])
        elif args.command == 'logs':
            c.run_tools([{"tool": "scan_local_logs"}])
        elif args.command == 'security':
            posture = c.run_single_tool("get_security_posture")
            if isinstance(posture, dict):
                from security_tools import render_posture_dashboard
                console.print(render_posture_dashboard(posture))
        elif args.command == 'chat':
            run_chat_mode(c, voice_layer)
        else:
            # Natural language — one-shot with full persona
            console.print(Panel(f"[bold cyan]Task:[/bold cyan] {args.command}", title="🤖 Jarvis", border_style="cyan"))
            try:
                brain, sys_prompt, extract_fn = _build_brain()
                with console.status("[bold cyan]🧠 Thinking...[/bold cyan]", spinner="dots"):
                    response_data = brain.chat([{"role": "user", "content": args.command}], sys_prompt)
                jarvis_response = _persist_remember_tags(response_data.get("response", ""), extract_fn)
                tools_to_run = response_data.get("tools", [])
                if jarvis_response:
                    console.print(f"[bold cyan]Jarvis:[/bold cyan] {jarvis_response}")
                if tools_to_run:
                    results = c.run_tools(tools_to_run)
                    with console.status("[bold cyan]🧠 Summarizing...[/bold cyan]", spinner="dots"):
                        final = brain.chat([
                            {"role": "user", "content": args.command},
                            {"role": "assistant", "content": jarvis_response or ""},
                            {"role": "user", "content": f"[SYSTEM] Tool results:\n{results}\n\nProvide your final response."}
                        ], sys_prompt)
                    final_text = _persist_remember_tags(final.get("response", ""), extract_fn)
                    if final_text:
                        console.print(f"[bold cyan]Jarvis:[/bold cyan] {final_text}")
            except Exception as e:
                console.print(f"[bold red]AI Brain Error:[/bold red] {e}")
        return

    # Interactive Menu Mode
    while True:
        print_banner()
        display_menu()

        choice = Prompt.ask("\nSelect an option", choices=["1","2","3","4","5","6","7","8"], default="6")

        if choice == '1':
            console.print(Panel("[bold cyan]Goal:[/bold cyan] Check recent emails", title="🤖 Jarvis", border_style="cyan"))
            c.run_tools([{"tool": "read_recent_emails"}])
            console.print(Panel("[bold green]Done[/bold green]", border_style="green"))
        elif choice == '2':
            console.print(Panel("[bold cyan]Goal:[/bold cyan] Check system health", title="🤖 Jarvis", border_style="cyan"))
            c.run_tools([{"tool": "check_system_health"}])
            console.print(Panel("[bold green]Done[/bold green]", border_style="green"))
        elif choice == '3':
            console.print(Panel("[bold cyan]Goal:[/bold cyan] Scan local logs", title="🤖 Jarvis", border_style="cyan"))
            c.run_tools([{"tool": "scan_local_logs"}])
            console.print(Panel("[bold green]Done[/bold green]", border_style="green"))
        elif choice == '4':
            console.print(Panel("[bold cyan]Goal:[/bold cyan] Ping localhost (Tier 3)", title="🤖 Jarvis", border_style="cyan"))
            c.run_tools([{"tool": "run_local_script", "args": {"command": "ping", "args": "127.0.0.1 -n 2"}}])
            console.print(Panel("[bold green]Done[/bold green]", border_style="green"))
        elif choice == '5':
            console.print(Panel("[bold cyan]Goal:[/bold cyan] Security posture check", title="🤖 Jarvis", border_style="cyan"))
            posture = c.run_single_tool("get_security_posture")
            if isinstance(posture, dict):
                from security_tools import render_posture_dashboard
                console.print(render_posture_dashboard(posture))
            console.print(Panel("[bold green]Done[/bold green]", border_style="green"))
        elif choice == '6':
            run_chat_mode(c, voice_layer)
        elif choice == '7':
            try:
                with open("data/episodic_memory.jsonl", "r") as f:
                    lines = f.readlines()
                table = Table(title="Recent Action Logs (Last 10)", border_style="cyan")
                table.add_column("Tier", justify="center", style="cyan", width=8)
                table.add_column("Action", style="magenta", width=22)
                table.add_column("Result", style="white")
                for line in lines[-10:]:
                    data = json.loads(line)
                    res = data['result']
                    if len(res) > 100:
                        res = res[:97] + "..."
                    table.add_row(data['tier'], data['action'], res)
                console.print(table)
            except Exception:
                console.print("[bold red]No memory found.[/bold red]")
            Prompt.ask("\nPress Enter to continue", default="")
        elif choice == '8':
            console.print("[bold green]Shutting down.[/bold green]")
            sys.exit(0)

if __name__ == "__main__":
    main()

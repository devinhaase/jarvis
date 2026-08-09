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

# Phase 6 item 8 (CLI/GUI consistency pass): identical to webapp/app.js's TEAM_ICON/
# TEAM_LABEL — same icons, same names, so "which team handled this" reads the same way
# whether you're looking at the terminal or the browser. Kept as a literal copy rather than
# a shared import specifically because these are presentation constants for two completely
# different rendering targets (Rich console markup vs. DOM text) — a shared source would
# need its own translation layer for no real benefit over just keeping both in sync by eye.
TEAM_ICON = {
    "personal_assistant": "🗂", "network": "📡", "it": "🖥️", "cybersecurity": "🛡️", "hacking": "🎯",
}
TEAM_LABEL = {
    "personal_assistant": "Personal Assistant", "network": "Network", "it": "IT",
    "cybersecurity": "Cybersecurity", "hacking": "Hacking",
}


def _build_jarvis_brain():
    """Phase 6 item 8: the CLI's standalone entry point onto the SAME JarvisBrain +
    team_router engine the multi-device server uses — session_manager.py's own docstring
    already documented this as the intent ("usable by both the CLI ... and the multi-device
    server"), but main.py never actually adopted it and kept a separate, older flat-tool
    ReAct loop instead. That meant running `python main.py chat` locally never got Phase 4's
    team routing or Phase 5's skill usage at all, silently diverging from what the same
    request does through the web GUI. approval_fn=None here isn't a gap — Coordinator's own
    default _request_approval() already implements exactly this CLI's local
    Confirm.ask()/3-second-countdown flow when no external approval_fn is given, so passing
    None reuses that unchanged rather than duplicating it in a second place."""
    from session_manager import JarvisBrain
    return JarvisBrain(approval_fn=None, memory=memory)


def _print_team_status(event: str, data):
    """Rich-console equivalent of webapp/app.js's tool_status handler — same three
    team_router.py events (team_routing/team_active/team_handoff_gate), same phrasing,
    printed as dim status lines rather than DOM notes. Also handles "tools_starting",
    already emitted by JarvisBrain.process_turn_stream itself (team-routed or not)."""
    if event == "team_routing":
        icons = "".join(TEAM_ICON.get(k, "🤖") for k in data["teams"])
        names = " → ".join(TEAM_LABEL.get(k, k) for k in data["teams"])
        how = "asked directly" if data.get("explicit") else "routed"
        console.print(f"[dim]{icons} {names} ({how})[/dim]")
    elif event == "team_active":
        team = data.get("team")
        console.print(f"[dim]{TEAM_ICON.get(team, '🤖')} {TEAM_LABEL.get(team, team)} team working…[/dim]")
    elif event == "team_handoff_gate":
        verdict = "approved" if data.get("approved") else "not approved"
        console.print(f"[dim yellow]⚠ Cybersecurity → Hacking handoff {verdict}[/dim yellow]")
    elif event == "tools_starting":
        tools = ", ".join(data or [])
        if tools:
            console.print(f"[dim](running {tools}…)[/dim]")


def run_team_turn(brain, chat_history: list, quiet: bool = False, on_final=None) -> dict:
    """Shared by chat mode and the one-shot natural-language path: runs one turn through
    team_router.route_and_run() (falls back to personal_assistant if classification is
    inconclusive — team_router's own default, unchanged) and streams the response to the
    console as it arrives, same UX the remote `--server` path and the web GUI already have,
    rather than local mode being the one place still waiting for a whole response to land
    at once. `quiet=True` (used by --json) suppresses all console output — the caller
    prints the structured result itself."""
    import team_router

    streaming = False

    def on_status(event, data):
        if not quiet:
            _print_team_status(event, data)

    result = {"response": "", "tools_ran": [], "denied": [], "teams_involved": []}
    for kind, payload in team_router.route_and_run(brain, chat_history, None, on_status=on_status):
        if kind == "chunk":
            if quiet:
                continue
            if not streaming:
                console.print()
                console.print("[bold cyan]Jarvis:[/bold cyan] ", end="")
                streaming = True
            console.print(payload, end="")
        elif kind == "done":
            result = payload
    if not quiet:
        if streaming:
            console.print()
        elif result.get("response"):
            console.print(f"[bold cyan]Jarvis:[/bold cyan] {result['response']}")
        for d in result.get("denied", []):
            console.print(f"[dim](refused: {d['tool']} needs '{d['required']}' capability)[/dim]")
        console.print()
    if on_final and result.get("response"):
        on_final(result["response"])
    return result


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
    table.add_row("[dim]── Teams & Skills ──[/dim]", "", "")
    table.add_row("", "7", "View teams (mirrors the web GUI's Teams panel)")
    table.add_row("", "8", "View skill review queue (mirrors the web GUI's Skills panel)")
    table.add_row("[dim]── Memory ──[/dim]", "", "")
    table.add_row("", "9", "View recent Action Logs")
    table.add_row("[dim]── System ──[/dim]", "", "")
    table.add_row("", "10", "Exit")

    console.print(Panel(table, title="[bold]Welcome, Devin.[/bold]", border_style="magenta"))

def run_chat_mode(voice_layer=None):
    """Interactive conversational chatbot loop — Phase 6 item 8: now the same JarvisBrain +
    team_router engine the web GUI and `--server` thin-client use (see _build_jarvis_brain's
    docstring for why this changed), not a separate flat-tool ReAct loop."""
    brain = _build_jarvis_brain()
    if not brain.is_available():
        console.print(f"[bold red]Failed to load AI Brain:[/bold red] {brain._llm_error}")
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

        speak_fn = voice_layer.speak if voice_active else None
        run_team_turn(brain, chat_history, on_final=speak_fn)

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


def _json_print(obj):
    print(json.dumps(obj, indent=2, default=str))


class _quiet_coordinator:
    """Phase 6 item 8: --json needs to mean *only* JSON on stdout, pipeable to `jq` or
    anything else — but coordinator.py's Reason/Act/Observe steps print directly via their
    own module-level Rich Console unconditionally, there's no quiet flag on Coordinator
    itself to pass through. Rather than thread a quiet param through Coordinator/
    JarvisBrain/team_router (every layer between here and there, for one CLI-only concern),
    redirect that one Console's output file to os.devnull for the duration of the call —
    the narrowest fix that doesn't touch behavior anything else depends on."""
    def __enter__(self):
        import coordinator as _coord
        self._orig_file = _coord.console.file
        # Real bug hit writing this: os.devnull opened with the platform default codepage
        # (cp1252 on Windows) still round-trips through real encode() calls even though the
        # bytes go nowhere — Rich's Console writes real text through self.file regardless of
        # where that file points, so an emoji in a Reason/Act/Observe line raised
        # UnicodeEncodeError before ever reaching /dev/null. Explicit UTF-8 (same
        # errors="replace" convention this file and server.py already use for stdout/stderr)
        # fixes it.
        self._devnull = open(os.devnull, "w", encoding="utf-8", errors="replace")
        _coord.console.file = self._devnull
        return self

    def __exit__(self, *exc):
        import coordinator as _coord
        _coord.console.file = self._orig_file
        self._devnull.close()


def main():
    parser = argparse.ArgumentParser(description="Jarvis Automation CLI")
    parser.add_argument("command", nargs="?", help="Direct command or natural language")
    parser.add_argument("--voice", action="store_true", help="Enable voice mode (requires voice deps)")
    parser.add_argument("--server", metavar="HOST:PORT", help="Connect to a Jarvis server as a thin client instead of running locally")
    parser.add_argument("--json", action="store_true",
                         help="Machine-readable output for a direct command (emails/health/logs/security) "
                              "or a one-shot natural-language request — no Rich formatting, no chat/menu modes.")
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
        if args.json and args.command in ("chat",):
            _json_print({"error": f"--json isn't meaningful for '{args.command}' (interactive mode) — use it with "
                                   "emails/health/logs/security, or a one-shot natural-language request."})
            sys.exit(2)

        if args.command in ('emails', 'health', 'logs'):
            tool_name = {'emails': 'read_recent_emails', 'health': 'check_system_health', 'logs': 'scan_local_logs'}[args.command]
            if args.json:
                with _quiet_coordinator():
                    result = c.run_single_tool(tool_name)
                _json_print({"tool": tool_name, "result": result})
            else:
                c.run_tools([{"tool": tool_name}])
        elif args.command == 'security':
            if args.json:
                with _quiet_coordinator():
                    posture = c.run_single_tool("get_security_posture")
                _json_print({"tool": "get_security_posture", "result": posture})
            else:
                posture = c.run_single_tool("get_security_posture")
                if isinstance(posture, dict):
                    from security_tools import render_posture_dashboard
                    console.print(render_posture_dashboard(posture))
        elif args.command == 'chat':
            run_chat_mode(voice_layer)
        else:
            # Natural language — one-shot, now through the same team-routed engine chat
            # mode and the web GUI use (Phase 6 item 8), not a separate hand-rolled
            # two-call chat()/tools/chat() sequence.
            if not args.json:
                console.print(Panel(f"[bold cyan]Task:[/bold cyan] {args.command}", title="🤖 Jarvis", border_style="cyan"))
            brain = _build_jarvis_brain()
            if not brain.is_available():
                msg = f"AI Brain unavailable: {brain._llm_error}"
                if args.json:
                    _json_print({"error": msg})
                else:
                    console.print(f"[bold red]AI Brain Error:[/bold red] {brain._llm_error}")
                sys.exit(1)
            chat_history = [{"role": "user", "content": args.command}]
            if args.json:
                with _quiet_coordinator():
                    result = run_team_turn(brain, chat_history, quiet=True)
                _json_print(result)
            else:
                run_team_turn(brain, chat_history, quiet=False)
        return

    # Interactive Menu Mode
    while True:
        print_banner()
        display_menu()

        choice = Prompt.ask("\nSelect an option", choices=[str(n) for n in range(1, 11)], default="6")

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
            run_chat_mode(voice_layer)
        elif choice == '7':
            # Phase 6 item 8: mirrors the web GUI's Teams panel — same fields (tool count,
            # scope, address phrase), same data source (teams.TEAMS), just a Rich table
            # instead of a modal.
            from teams import TEAMS
            table = Table(title="Teams", border_style="cyan")
            table.add_column("Team", style="bold cyan")
            table.add_column("Tools", justify="center", style="magenta", width=6)
            table.add_column("Scope", style="white", max_width=50)
            table.add_column("Address with", style="dim")
            for key, team in TEAMS.items():
                # user_summary, not scope_prompt — scope_prompt is written as an instruction
                # to the model itself ("You are the Network team..."), not something to show
                # a person; same fix applied to the GUI's Teams panel (see server.py's
                # _team_catalog()).
                table.add_row(
                    f"{TEAM_ICON.get(key, '🤖')} {team.display_name}",
                    str(len(team.tool_names())), team.user_summary,
                    f'"ask the {team.aliases[0]}..."' if team.aliases else "",
                )
            console.print(table)
            Prompt.ask("\nPress Enter to continue", default="")
        elif choice == '8':
            # Phase 6 item 8: mirrors the web GUI's Skills panel, grouped the same way
            # (proposed first — that's the actual review queue).
            import skills
            table = Table(title="Skill Review Queue", border_style="cyan")
            table.add_column("Status", style="bold", width=10)
            table.add_column("Name", style="cyan")
            table.add_column("Team", style="magenta", width=18)
            table.add_column("Tier", justify="center", width=8)
            table.add_column("Record", justify="center", width=10)
            status_style = {"proposed": "yellow", "active": "green", "flagged": "red", "disabled": "dim", "rejected": "dim"}
            all_skills = sorted(skills.list_skills(), key=lambda s: (s.status != "proposed", s.status))
            if not all_skills:
                console.print("[dim]No skills learned yet.[/dim]")
            for s in all_skills:
                style = status_style.get(s.status, "white")
                table.add_row(
                    f"[{style}]{s.status.upper()}[/{style}]", s.name,
                    f"{TEAM_ICON.get(s.team, '🤖')} {TEAM_LABEL.get(s.team, s.team or '—')}",
                    s.tier, f"{s.success_count}/{s.success_count + s.fail_count}",
                )
            console.print(table)
            Prompt.ask("\nPress Enter to continue", default="")
        elif choice == '9':
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
        elif choice == '10':
            console.print("[bold green]Shutting down.[/bold green]")
            sys.exit(0)

if __name__ == "__main__":
    main()

import sys
import time

# This module's console.print() calls include emoji (🤔, ⚡, 👀, ...) as a matter of
# course. main.py and server.py each reconfigure stdout/stderr to UTF-8 at their own
# entry point, but that only protects processes that start there — any other entry point
# (a test, a script, a REPL) that imports Coordinator directly inherits whatever codepage
# the console happened to start in, and on a legacy Windows codepage that's a crash the
# moment a tool actually runs, not a cosmetic glitch. Doing it here too, at the actual
# source of the emoji output, means it's fixed regardless of what imported this module.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from memory import Memory
from tools import ALL_TOOLS, Tier
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from security_hardening import is_kill_switch_armed, kill_switch_status, redact, KillSwitchActive

console = Console()


_KILL_SWITCH_EXEMPT = {"disarm_kill_switch"}  # must always be reachable, or arming it is one-way


def _check_kill_switch(tool):
    """Phase 8: the kill switch is checked at the same dispatch point offense-authorization
    already is — a single choke point every execution path goes through, not something a
    caller could bypass by calling a different entrypoint. Tier 1 (read-only) tools still
    run while armed — the point is to stop *actions*, not to also break the ability to ask
    Jarvis what's going on. disarm_kill_switch is exempt regardless of its own tier — a
    Tier-2 tool that the kill switch itself blocks would make arming it a one-way door."""
    if tool.tier == Tier.TIER_1 or tool.name in _KILL_SWITCH_EXEMPT or not is_kill_switch_armed():
        return
    status = kill_switch_status()
    raise KillSwitchActive(
        f"Kill switch is ARMED (reason: {status.get('reason', '?')}) — "
        f"'{tool.name}' (Tier {tool.tier.name}) was refused. Call disarm_kill_switch to resume."
    )

# Common parameter names offense-tagged tools use for "the thing being scanned/queried" —
# scan_ports/run_recon_pipeline use "target", shodan_lookup uses "query", dns_recon/
# subdomain_enum use "domain", check_ssl_cert uses "host". Checked in order; a tool with
# none of these (run_privesc_enum takes no target at all — it's local-only, always
# authorized, by design) is simply not gated here, matching its own docstring.
#
# Deliberately NOT included: "url" (tech_fingerprint, http_security_headers_audit). A URL
# like "https://example.com/path" won't exact-match a bare hostname entry in
# authorized_targets.json, so gating it here would produce false *rejections* of
# legitimately authorized targets, not a security gap — those two tools extract the
# hostname themselves (urlparse) and run their own AuthorizationCheck with that, same
# defense-in-depth pattern, just handled where the URL-to-host parsing actually belongs.
_OFFENSE_TARGET_ARG_NAMES = ("target", "query", "domain", "host")


def _check_offense_authorization(tool, args: dict):
    """Defense-in-depth (Phase 3e): every current offense tool already checks
    AuthorizationCheck itself (auth_check.py) — this re-checks at the dispatch layer so a
    *future* offense-tagged tool that forgets to call it still can't run unchecked. Built
    fresh per call, not held at import time: a missing/edited authorized_targets.json
    should fail this one call (same as it already fails the tool's own internal check),
    not crash coordinator.py — and therefore the whole agent — on import.
    Raises AuthorizationError (via auth_check) if the target isn't on the allowlist.
    """
    if getattr(tool, "role", None) != "offense" or not args:
        return
    target = next((args[k] for k in _OFFENSE_TARGET_ARG_NAMES if args.get(k)), None)
    if not target:
        return
    from auth_check import AuthorizationCheck
    AuthorizationCheck().is_authorized(target)


def _autofill_created_by_team(tool_name: str, args: dict, episode_team):
    """Phase 7: create_team_incident's required `created_by_team` argument asks the model
    to correctly self-report which team it's currently acting as — found live-testing this
    item that a real local model reliably omits it (`_create_team_incident() missing 1
    required positional argument`), even though the coordinator already *knows* the answer
    (it's exactly `episode_team`, the same value now threaded through for episode/approval
    attribution). Filling it in here — only when the model left it out, never overriding an
    explicit value — turns a hard failure into a call that just works, using data this
    dispatch layer already has rather than trusting the model to repeat it back correctly."""
    if tool_name == "create_team_incident" and episode_team and not args.get("created_by_team"):
        args["created_by_team"] = episode_team


class Coordinator:
    def __init__(self, approval_fn=None, memory=None):
        """
        approval_fn: optional callable(action_name, tier) -> bool, overriding the default
        local-console approval flow (Confirm.ask / time.sleep below). The multi-device server
        passes one that routes the request to a connected client instead of local stdin —
        console.Confirm.ask() has no meaning on a machine nobody's sitting at.
        memory: optional pre-built Memory instance to share (the server holds one Memory for
        every session, rather than each session's Coordinator writing to its own).
        """
        self.memory = memory or Memory()
        self._external_approval_fn = approval_fn

    def _request_approval(self, action_name, tier, team=None):
        """`team` (Phase 7): which team's turn this approval belongs to, for the Overseer
        dashboard's per-team "waiting on me" queue and the push notification's deep-link
        target — optional and backward-compatible on purpose. Every approval_fn written
        before this phase (server.py's original signature, several tests' `lambda a, t:
        True`) takes exactly 2 positional args; rather than force every one of them to grow
        a third parameter for a feature only the real server-side implementation actually
        uses, this tries the 3-arg form first and falls back to the original 2-arg call on
        TypeError — the same "evolve a callback contract without breaking existing callers"
        shape a public API would use, just applied to an in-process callable."""
        if self._external_approval_fn is not None:
            try:
                return self._external_approval_fn(action_name, tier, team=team)
            except TypeError:
                return self._external_approval_fn(action_name, tier)

        if tier == Tier.TIER_4:
            console.print(f"\n[bold red][Tier 4][/bold red] Permission requested to execute '[bold]{action_name}[/bold]'.")
            return Confirm.ask("Approve execution?")
        elif tier == Tier.TIER_3:
            console.print(f"\n[bold yellow][Tier 3][/bold yellow] Preparing to execute '[bold]{action_name}[/bold]'.")
            with console.status("[yellow]Proceeding in 3 seconds. Press Ctrl+C to cancel...[/yellow]"):
                try:
                    time.sleep(3)
                    return True
                except KeyboardInterrupt:
                    console.print("\n[bold red][User Cancelled][/bold red] Execution aborted.")
                    return False
        return True

    def run_tools(self, plan: list, tool_subset: set = None, team: str = None) -> str:
        """
        Execute a list of tool steps.
        Returns a formatted string of all results to feed back to the LLM.

        `tool_subset` (Phase 4): if given, any tool_name not in it is treated exactly like
        an unknown tool — refused, not executed. This is the real enforcement point for a
        team's scope; llm.py hiding other tools from the prompt is just the first layer
        (a model can still hallucinate a call to something it wasn't shown), same
        defense-in-depth pattern _check_offense_authorization already uses below.

        `team` (Phase 7): which team's turn this is, for episode/approval attribution on
        the Overseer dashboard. Deliberately the *caller's* active-team context, not
        `tool.team` — a coordinator-level tool (team=None on the Tool itself, e.g. the kill
        switch) still ran as part of some specific team's turn, and that's what a dashboard
        needs to show it under, not "no team." Falls back to `tool.team` only when the
        caller didn't say (e.g. a non-team-routed direct call) — still better than nothing.
        """
        results_summary = ""

        for step in plan:
            tool_name = step['tool']
            args = step.get('args', {})

            tool = ALL_TOOLS.get(tool_name)
            if not tool:
                msg = f"Error: Tool '{tool_name}' not found."
                console.print(f"[bold red]{msg}[/bold red]")
                results_summary += f"{msg}\n"
                break
            if tool_subset is not None and tool_name not in tool_subset:
                msg = f"Error: '{tool_name}' is outside this team's scope for this turn — not executed."
                console.print(f"[bold red]{msg}[/bold red]")
                results_summary += f"{msg}\n"
                break
            episode_team = team if team is not None else tool.team
            _autofill_created_by_team(tool_name, args, episode_team)

            # 1. REASON
            console.print(f"\n[bold magenta]🤔 Reason:[/bold magenta] Using [bold]{tool_name}[/bold] (Tier {tool.tier.name})")

            try:
                _check_kill_switch(tool)
            except KillSwitchActive as e:
                console.print(f"[bold red]🛑 Kill switch:[/bold red] {e}")
                self.memory.log_episode(tool_name, str(e), tool.tier.name, team=episode_team, outcome="blocked")
                results_summary += f"{tool_name}: {e}\n"
                break

            # 2. ACT
            if not self._request_approval(tool_name, tool.tier, team=episode_team):
                msg = f"Execution of '{tool_name}' denied/cancelled by user."
                console.print(f"[bold red]⛔ Act:[/bold red] {msg}")
                self.memory.log_episode(tool_name, "Cancelled by user", tool.tier.name, team=episode_team, outcome="denied")
                results_summary += f"{tool_name}: {msg}\n"
                break

            if args:
                console.print(f"[bold blue]⚡ Act:[/bold blue] Executing '{tool_name}' with args: {args}")
            else:
                console.print(f"[bold blue]⚡ Act:[/bold blue] Executing '{tool_name}'")

            # 3. OBSERVE
            try:
                _check_offense_authorization(tool, args)
                with console.status(f"[bold green]Running {tool_name}...[/bold green]", spinner="dots"):
                    result = tool.execute(**args)

                safe_result_str = redact(str(result).encode('ascii', 'ignore').decode('ascii'))
                display_str = safe_result_str[:1200] + "\n...[truncated]" if len(safe_result_str) > 1200 else safe_result_str
                console.print(f"[bold green]👀 Observe:[/bold green]\n{display_str}")
                self.memory.log_episode(tool_name, safe_result_str, tool.tier.name, team=episode_team, outcome="success")
                results_summary += f"{tool_name} SUCCESS:\n{safe_result_str}\n\n"
            except Exception as e:
                err_msg = str(e)
                console.print(f"[bold red]❌ Observe (FAILURE):[/bold red] {err_msg}")
                self.memory.log_episode(tool_name, f"ERROR: {err_msg}", tool.tier.name, team=episode_team, outcome="failed")
                results_summary += f"{tool_name} FAILED: {err_msg}\n"
                console.print("[bold red]🛑 Reflect:[/bold red] Tool failed. Halting to prevent cascading errors.")
                break

        return results_summary.strip()

    def run_single_tool(self, tool_name: str, args: dict = None, team: str = None):
        """
        Execute exactly one tool through the same Reason -> Act -> Observe flow
        (approval + logging) as run_tools, but return the raw result object
        instead of a stringified summary — for callers that need structured
        data (e.g. rendering a dashboard) rather than LLM-facing text.
        Returns None if the tool isn't found, approval is denied, or execution fails
        (each case is already printed/logged before returning). `team`: see run_tools.
        """
        args = args or {}
        tool = ALL_TOOLS.get(tool_name)
        if not tool:
            console.print(f"[bold red]Error: Tool '{tool_name}' not found.[/bold red]")
            return None
        episode_team = team if team is not None else tool.team
        _autofill_created_by_team(tool_name, args, episode_team)

        console.print(f"\n[bold magenta]🤔 Reason:[/bold magenta] Using [bold]{tool_name}[/bold] (Tier {tool.tier.name})")

        try:
            _check_kill_switch(tool)
        except KillSwitchActive as e:
            console.print(f"[bold red]🛑 Kill switch:[/bold red] {e}")
            self.memory.log_episode(tool_name, str(e), tool.tier.name, team=episode_team, outcome="blocked")
            return None

        if not self._request_approval(tool_name, tool.tier, team=episode_team):
            msg = f"Execution of '{tool_name}' denied/cancelled by user."
            console.print(f"[bold red]⛔ Act:[/bold red] {msg}")
            self.memory.log_episode(tool_name, "Cancelled by user", tool.tier.name, team=episode_team, outcome="denied")
            return None

        try:
            _check_offense_authorization(tool, args)
            with console.status(f"[bold green]Running {tool_name}...[/bold green]", spinner="dots"):
                result = tool.execute(**args)
            safe_result_str = redact(str(result).encode('ascii', 'ignore').decode('ascii'))
            self.memory.log_episode(tool_name, safe_result_str, tool.tier.name, team=episode_team, outcome="success")
            return result
        except Exception as e:
            err_msg = str(e)
            console.print(f"[bold red]❌ Observe (FAILURE):[/bold red] {err_msg}")
            self.memory.log_episode(tool_name, f"ERROR: {err_msg}", tool.tier.name, team=episode_team, outcome="failed")
            return None

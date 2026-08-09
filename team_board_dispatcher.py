"""
team_board_dispatcher.py — Phase 4: the piece that actually satisfies "a Network team
finding can trigger a Cybersecurity team response without going through Devin each time."
Same background-loop shape as posture_monitor.py (poll on an interval, post into a
dedicated conversation, broadcast so the GUI notices) — deliberately not a new pattern.

Polls conversation_store's team_incidents table (Phase 4's shared board) for open items
with a target_team set, routes each one directly to that team (bypassing team_router's
classification stage — the target is already known), runs that team's normal scoped
JarvisBrain loop against a synthesized message describing the incident, posts the result
into a dedicated "Team Board" conversation, and marks the incident acknowledged.

Still goes through every normal safety mechanism a user-triggered turn would: Tier 2+
tools inside that team's response still need approval (broadcast to connected devices,
same as always), offense-authorization still gates any Hacking-team tool. Autonomous
dispatch means "no human had to notice and ask" — it does not mean "no human has to
approve a sensitive action."
"""

import os
import asyncio

from conversation_store import store
from teams import TEAMS

BOARD_CONV_FILE = os.path.join("data", ".team_board_conversation_id")
DEFAULT_INTERVAL_SECONDS = 5 * 60


def _get_or_create_board_conversation() -> str:
    if os.path.exists(BOARD_CONV_FILE):
        with open(BOARD_CONV_FILE, 'r') as f:
            cid = f.read().strip()
        if cid and store.conversation_exists(cid):
            return cid
    cid = store.create_conversation(device_id="team-board-dispatcher", title="Team Board")
    store.rename_conversation(cid, "Team Board")
    os.makedirs(os.path.dirname(BOARD_CONV_FILE) or ".", exist_ok=True)
    with open(BOARD_CONV_FILE, 'w') as f:
        f.write(cid)
    return cid


async def _dispatch_one_incident(brain, incident: dict, broadcast_all=None, set_team_status=None):
    target_team = incident.get("target_team")
    team = TEAMS.get(target_team)
    if team is None:
        # Unknown/broadcast-target incident — nothing to auto-dispatch to, leave it open
        # for a human (or a future team) to pick up explicitly.
        return

    conv_id = _get_or_create_board_conversation()
    synthetic_message = (
        f"[Team board] Open {incident.get('severity', 'info')} incident from "
        f"{incident.get('created_by_team', 'unknown')} team: {incident['title']}\n"
        f"{incident.get('description', '')}"
    )
    chat_history = [{"role": "user", "content": synthetic_message}]

    # Phase 7: dependency-injected the same way broadcast_all already is (posture_monitor.py/
    # daily_briefing.py's own established shape) rather than importing server.py directly —
    # this module is a background loop server.py starts, not the reverse; a direct import
    # would be circular. Flips the target team's Overseer tile to "working" for the duration
    # of this exact "one team's output triggered another team's action" flow, which is the
    # whole reason Phase 7 exists.
    if set_team_status:
        await set_team_status(target_team, "working", f"auto-dispatched: {incident['title']}")

    result = {"response": "", "tools_ran": [], "denied": []}
    try:
        result = brain.process_turn(
            chat_history, device_capabilities=None, max_iterations=3,
            conversation_id=conv_id, tool_subset=team.tool_names(), team_context=team.scope_prompt,
            team_key=target_team,
        )
    except Exception as e:
        result = {"response": f"({team.display_name} team's auto-dispatch failed: {e})", "tools_ran": [], "denied": []}

    text = f"{team.display_name} team responding to incident \"{incident['title']}\":\n\n{result.get('response', '')}"
    store.add_message(conv_id, "assistant", text, source="text")
    store.update_incident_status(incident["id"], "acknowledged")

    if set_team_status:
        await set_team_status(target_team, "idle", "")

    if broadcast_all:
        await broadcast_all({
            "type": "stream_end", "conversation_id": conv_id,
            "full_text": text, "tools_ran": [t.get("tool") for t in result.get("tools_ran", [])],
            "denied": result.get("denied", []),
        })
        await broadcast_all({"type": "conversation_list_changed"})
        # Phase 7: this is exactly "one team's output triggered another team's action" —
        # the Overseer's cross-team log needs to know right away, not on its next poll.
        await broadcast_all({"type": "cross_team_incident", "incident_id": incident["id"],
                              "from_team": incident.get("created_by_team"), "to_team": target_team})


async def team_board_dispatch_loop(brain, broadcast_all=None, interval_seconds: int = None, set_team_status=None):
    """Runs forever (until cancelled by server.py's lifespan on shutdown). Each cycle:
    fetch open incidents with a target_team, dispatch each to that team's scoped loop, mark
    acknowledged. A failure in one incident's dispatch doesn't stop the loop or block the
    rest of the queue — same per-cycle exception isolation posture_monitor_loop uses."""
    interval = interval_seconds or int(os.getenv("JARVIS_TEAM_BOARD_INTERVAL_SECONDS", str(DEFAULT_INTERVAL_SECONDS)))
    while True:
        try:
            open_incidents = [i for i in store.list_incidents(status="open") if i.get("target_team")]
            for incident in open_incidents:
                try:
                    await _dispatch_one_incident(brain, incident, broadcast_all, set_team_status)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    print(f"[TeamBoard] dispatch failed for incident {incident.get('id')}: {e}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[TeamBoard] poll cycle failed: {e}")
        await asyncio.sleep(interval)

# Jarvis — Security Incident Runbook

What to actually do if something looks wrong: an unexpected tool call, a device you don't
recognize connected, a tool result that looks like it's leaking a secret, or Jarvis appears
to be acting on instructions nobody gave it. Written for the one-person LAN deployment this
project actually is — not a 24/7 SOC playbook.

## 1. Stop everything, immediately

From any connected device (GUI, CLI, voice), say or type:

> arm the kill switch, reason: <why>

This calls `arm_kill_switch()` (Tier 1 — never blocked by anything, including itself). Every
Tier 2+ tool call is refused from that point on, on every device, until disarmed. Tier 1
(read-only) tools keep working so you can still ask Jarvis what happened.

If the agent process itself is unresponsive and you can't reach it through any client, arm
it by hand instead — create an empty file at:

```
data/KILL_SWITCH
```

Same effect; `is_kill_switch_armed()` just checks whether that file exists.

## 2. Figure out what happened

- `run_self_audit()` — checks kill-switch state, whether credential files are accidentally
  tracked in git, and registered device count/staleness.
- Check `data/episodic_memory.jsonl` for the tool-call log (tool name, tier, redacted
  result) — this is the ground truth of what actually executed, independent of what the
  conversation transcript claims happened.
- `python server.py --list-devices` (or ask Jarvis) to see every registered device. Anything
  you don't recognize:
  ```
  python server.py --revoke <device_id>
  ```
  Revocation takes effect immediately (`DeviceRegistry.validate()` reloads from disk on
  every check — no server restart needed).

## 3. If it looks like prompt injection

Phase 7 already delimits tool output as untrusted (`<<<TOOL_OUTPUT>>>`) and pattern-matches
for override phrasings before the model sees it — but that detector is probabilistic, not a
guarantee (documented as a known limitation since Phase 7, not new to this runbook). If
Jarvis appears to have followed an instruction embedded in a tool result, a file, or a web
page rather than something you actually typed:

1. Arm the kill switch.
2. Check which tool call pulled in the untrusted content (episodic log).
3. That content source (a file, a site, an email) is the actual incident, not Jarvis — treat
   it the same as you would any other untrusted input you now know is hostile.

## 4. If a secret looks like it leaked into a log or a chat reply

`redact()` (security_hardening.py) scrubs common secret shapes — API keys, bearer tokens,
private key blocks, anything matching `key=`/`token=`/`password=` — out of tool output
before it's logged to episodic memory or shown back to the LLM as history. It is pattern
matching, not a guarantee, same honesty as the injection detector above. If something slips
through:

1. Rotate the actual credential first — assume it's compromised, don't wait to confirm.
2. `create_backup()` still has the old episodic log if you need it for forensics — restoring
   *from* backup is deliberately a manual CLI-only action (never an agent tool), so a
   compromised session can't touch your backups.

## 5. Resume

```
disarm the kill switch
```

(`disarm_kill_switch`, Tier 2 — deliberately not Tier 1, so resuming after a real incident
takes one more beat than stopping does. Exempt from the kill switch's own block, or arming
it would be a one-way door.)

## Known scope boundaries (deliberate, not gaps someone forgot)

- The kill switch is file-based and per-process — it stops tool *execution*, it does not
  kill the server process itself or revoke any device's token. Pair it with a device revoke
  if the actual threat is a specific compromised client, not "something is misbehaving."
- Rate limiting on device auth (`AuthRateLimiter`) is in-memory, per server process — a
  restart clears any active lockout. Acceptable for this threat model (slowing down
  automated guessing on a LAN), not designed to survive a distributed attack.
- `/upload`, `/transcribe`, and `/export/{id}` are unauthenticated HTTP endpoints by current
  design (only the WebSocket handshake enforces device tokens) — a known, pre-existing scope
  boundary from Phase 3/4, not something this pass changed. Worth closing in a future pass if
  this server is ever exposed beyond a trusted LAN.

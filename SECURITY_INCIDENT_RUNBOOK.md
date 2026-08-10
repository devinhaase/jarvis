# Jarvis — Security Incident Runbook

What to actually do if something looks wrong: an unexpected tool call, a device you don't
recognize connected, a tool result that looks like it's leaking a secret, or Jarvis appears
to be acting on instructions nobody gave it. Written for the one-person LAN deployment this
project actually is — not a 24/7 SOC playbook.

## 1. Stop everything, immediately

There's no in-band "kill switch" tool call anymore (removed at Devin's explicit request —
see task.md's entry for the removal and the reasoning). The actual stop mechanisms:

- **Revoke the specific device** that's misbehaving — this is immediate and doesn't need the
  agent's cooperation:
  ```
  python server.py --revoke <device_id>
  ```
  Revocation takes effect immediately (`DeviceRegistry.validate()` reloads from disk on
  every check — no server restart needed). This is the right first move for "one specific
  client is compromised or acting up."
- **Stop the server process** (Ctrl+C in its console, or stop the service if it's running as
  one) if the problem isn't isolated to one device, or the server itself is unresponsive.
  This is the real, unconditional "everything stops" — every Tier still requires the process
  to be running to do anything at all.
- Every Tier 2+ tool call already needs a per-call approval (broadcast to connected devices)
  before it executes — declining that approval stops that one call without touching anything
  else, which covers most "wait, don't do that" moments without needing a bigger hammer.

## 2. Figure out what happened

- `run_self_audit()` — checks whether credential files are accidentally tracked in git, and
  registered device count/staleness.
- Check `data/episodic_memory.jsonl` for the tool-call log (tool name, tier, redacted
  result) — this is the ground truth of what actually executed, independent of what the
  conversation transcript claims happened.
- `python server.py --list-devices` (or ask Jarvis) to see every registered device. Anything
  you don't recognize:
  ```
  python server.py --revoke <device_id>
  ```

## 3. If it looks like prompt injection

Phase 7 already delimits tool output as untrusted (`<<<TOOL_OUTPUT>>>`) and pattern-matches
for override phrasings before the model sees it — but that detector is probabilistic, not a
guarantee (documented as a known limitation since Phase 7, not new to this runbook). If
Jarvis appears to have followed an instruction embedded in a tool result, a file, or a web
page rather than something you actually typed:

1. Revoke the device the suspicious turn came from, or stop the server (see step 1) if you
   need to think before anything else runs.
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

Restart the server process (if you stopped it), and re-pair any device you revoked (same
first-connection flow as any new device — `device_registry.py`'s `register()`) once you're
satisfied the incident is over. There's no separate "resume" step beyond that — normal
per-call approval is already back in effect the moment the process is running.

## Known scope boundaries (deliberate, not gaps someone forgot)

- Revoking a device stops *that device's* future calls; it doesn't retroactively cancel
  anything already in flight, and it doesn't rotate any credential the agent itself holds
  (API keys, tokens) — pair it with a credential rotation (step 4) if that's the actual
  threat, not just "something is misbehaving."
- Rate limiting on device auth (`AuthRateLimiter`) is in-memory, per server process — a
  restart clears any active lockout. Acceptable for this threat model (slowing down
  automated guessing on a LAN), not designed to survive a distributed attack.
- `/upload`, `/transcribe`, and `/export/{id}` are unauthenticated HTTP endpoints by current
  design (only the WebSocket handshake enforces device tokens) — a known, pre-existing scope
  boundary from Phase 3/4, not something this pass changed. Worth closing in a future pass if
  this server is ever exposed beyond a trusted LAN.

## `authorized_targets.json` already supports network ranges (Phase 8)

Worth stating plainly since it's easy to assume otherwise: the Hacking team's
`data/authorized_targets.json` has supported CIDR ranges in its `"networks"` field since
before Phase 8 — `auth_check.py`'s `AuthorizationCheck.is_authorized()` checks any IP-literal
target against every CIDR string there via `ipaddress.ip_network(...)` membership, and
Devin's home `/24` (`192.168.1.0/24`) is already listed. This means "authorize a whole
segment, not just individual hosts" was never something that needed building for Phase 8's
expanded firewall/NAS-testing tooling — it's the same mechanism every offense tool already
checks against, unchanged. Two things this does NOT do, on purpose, matching every other
authorization boundary in this project: it only matches IP-literal targets (a bare hostname
still needs an exact entry in `"hosts"`/`"domains"`), and it's never edited by Jarvis itself
— adding a new range is always Devin's own explicit edit to the file, never inferred from a
conversation.

Feeding firewall/NAS testing techniques into the Hacking team's reference library
(`add_reference_material`) and drafting a tool from that material
(`synthesize_tool_from_reference`) also needed no new code for the same reason — both were
already generic (any goal + any reference content, not hacking-specific in mechanism), and
`synthesize_tool_from_reference` still only ever writes a file and proposes a skill
(status `"proposed"`, never auto-active) — Phase 5's review gate applies exactly as before.

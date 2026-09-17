# Remote access over VPN (Phase 6 item 7)

## Audit: does anything special-case network origin/IP in a way that would break or
weaken remote access?

**Authentication itself: no.** `registry.validate(device_id, token)` in `device_registry.py`
checks the bearer token against `devices.json` — nothing about that check depends on
`websocket.client.host`, the request's origin, or where it came from. A device with a valid
token authenticates identically whether it's on `127.0.0.1`, the LAN, or reaching the server
over a VPN. `server.py` binds `0.0.0.0` by default (`JARVIS_SERVER_HOST`), not `127.0.0.1` —
already reachable from anywhere that can route to it, no code change needed for that part.

**Rate limiting: yes, one deliberate exemption, added this session.** `_auth_limiter`
(`security_hardening.AuthRateLimiter`, Phase 8a) locks out a *source IP* after 5 failed hello
attempts in 5 minutes. Found live-testing Phase 6 item 6: a stale client (a second browser
tab or PWA session holding an old/mismatched token — see item 5's task.md entry for how that
can happen) kept retrying and failing from `127.0.0.1`, and because the lockout is IP-keyed,
it locked out *every* localhost client, including the real web GUI, repeatedly — a fresh
lockout every ~5 minutes for as long as the stale client kept retrying. Fixed with
`server.py`'s `_is_loopback()`: loopback (`127.0.0.0/8`, `::1`) is now exempt from the
lockout and failure counting entirely, on the reasoning that an attacker who can already
reach `127.0.0.1` on this machine has local code execution on the server itself — at which
point `devices.json`'s plaintext tokens are directly readable anyway, so rate-limiting
loopback buys no real security. **This exemption does not extend to the LAN or a VPN** — a
phone on the tailnet or the local network is still fully subject to the 5-attempts/300s
lockout, same as before. Token validation itself (the actual authentication) is completely
unaffected either way; only the failed-attempt lockout is loopback-exempt.

**CORS: no explicit policy configured, and that's correct as-is.** FastAPI without
`CORSMiddleware` sends no CORS headers, which is the *safe* default — it stops an arbitrary
third-party website from calling this API cross-origin via a visitor's browser. It doesn't
affect this app's own web GUI (served same-origin from this same server) or a WS client like
`main.py --server`/`voice_companion.py` (not subject to CORS at all — that's a browser
fetch/XHR restriction). Nothing to change here for remote access.

**Client code: no hardcoded `localhost`.** `webapp/app.js` builds its WS URL from
`location.host`/`location.protocol` dynamically — opening the page via a Tailscale hostname
or LAN IP connects back to that same address, not a baked-in `localhost`. `main.py --server`
and `voice_companion.py --server` both already accept an arbitrary `HOST:PORT`.

## The real gap found: push notifications and PWA install need a secure context

Chrome (and every other browser) only allows service worker registration and
`pushManager.subscribe()` on a "secure context": HTTPS, or literally `localhost`/`127.0.0.1`.
A plain `http://192.168.1.183:8765` — which is exactly how Devin's phone reaches this server
today, per the real server logs — **is not a secure context**. That means Phase 6 item 6's
push notifications (and installing the PWA at all, which also requires a registered service
worker) may never have been able to fully work from the phone over plain LAN HTTP, VPN or
not — this was already true before this session, not introduced by item 6, but item 6 is
what makes it actually matter.

**Tailscale actually solves this directly, not just the "remote access" part.** Tailscale
issues real HTTPS certificates for each tailnet device's MagicDNS name via `tailscale cert`
(or the simpler `tailscale serve https / 8765` / `tailscale funnel` for a one-command
reverse proxy) — once that's set up, `https://<this-machine>.<your-tailnet>.ts.net` is a
genuine secure context, everywhere on the tailnet, phone included. The recommendation below
uses this precisely because it fixes remote access AND the push-notification secure-context
gap with the same one-time setup, rather than treating them as two separate problems.

## Setup (Devin's own action — not something this session can do on his behalf)

1. Install Tailscale on the machine running `server.py` (`https://tailscale.com/download`),
   sign in, note its tailnet name (e.g. `yourname.ts.net`).
2. Install the Tailscale app on the phone, sign into the *same* tailnet.
3. On the server machine, enable HTTPS for this service:
   `tailscale cert <machine-name>.<tailnet>.ts.net` generates a cert/key pair, or simpler —
   `tailscale serve --bg https / http://localhost:8765` proxies real HTTPS straight to the
   already-running plain-HTTP server, no code change needed here at all.
4. On the phone, open `https://<machine-name>.<tailnet>.ts.net` (not the LAN IP) — this is
   now a secure context, so "Install app" and push notifications' `pushManager.subscribe()`
   have what they need to actually work.
5. Devices connect with their existing `devices.json` token exactly as before — Tailscale is
   pure network-layer transport, nothing about this app's own auth changes.

## Testing (the part only Devin can actually do)

- **Connect over Tailscale from outside the home network** (e.g., phone on cellular data,
  Tailscale connected) and confirm full GUI functionality — chat, tool calls, approvals.
- **Confirm the same request without Tailscale connected correctly fails** — Tailscale's
  whole point is that the service isn't reachable from the open internet at all outside the
  tailnet; this should just time out/fail to connect, not degrade to something insecure.
- **Re-verify item 6's phone push notification test** once accessed over the `https://` tailnet
  URL — item 6 could only be verified up to the server-sends-a-real-push step in this
  session's sandboxed browser; this is the step that actually needed a real phone anyway,
  and now also needs the secure-context fix above to even attempt subscribing.

## Local HTTPS on the LAN (so VPN is only needed away from home)

The setup above made `ai.dhaaselab.com` (over Tailscale) the *only* secure context this app
had, which meant mic input, push notifications, and PWA install all silently required being
connected to the VPN — even at home, on the same network as the machine running the server.
That's backwards: VPN should only be needed when actually away from home. The fix is a
*second* real cert, issued for the LAN IP itself via [mkcert](https://github.com/FiloSottile/mkcert)
(a local, self-signed CA you trust once per device — not a public ACME cert, since a public CA
won't issue for a private IP).

**One-time setup on the machine running the server (Devin's own action):**

1. Install mkcert: `choco install mkcert` (or download the release exe) and run `mkcert -install`
   once — this creates and trusts a local root CA on this machine.
2. Generate the LAN cert (swap the IP if it's ever different from the current
   `192.168.1.147`; reserve it as a DHCP static lease in the router so it doesn't drift):
   ```
   cd caddy
   mkcert -cert-file lan_cert.pem -key-file lan_key.pem 192.168.1.147
   ```
   `lan_cert.pem`/`lan_key.pem` are gitignored — regenerate them on any machine that ever
   runs this Caddy config, never commit them.
3. `caddy/Caddyfile` already has a second site block for `https://192.168.1.147:8443` using
   this cert (added alongside the existing `ai.dhaaselab.com` block) — `start_caddy.ps1`
   picks up both automatically, no separate process needed.
4. Open Windows Firewall to that port on the private/home profile: allow inbound TCP 8443
   for `caddy.exe` (same idea as the existing 8765 rule for `server.py` itself).

**One-time setup per device (phone, laptop) — trust the local CA:**

- mkcert's root CA is a single file: run `mkcert -CAROOT` on the server machine to find it
  (usually `rootCA.pem`), then transfer it to each device and install it as a trusted root
  certificate (Android: Settings → Security → Encryption & credentials → Install a
  certificate → CA certificate; Windows/macOS: double-click → install into the system/login
  Trusted Root store). This is a one-time step per device, not per connection.
- The Android app now tries `https://192.168.1.147:8443` first, falls back to plain
  `http://192.168.1.147:8765` (e.g. before the CA is trusted yet), and only reaches for the
  VPN domain if neither local address answers — see `ConnectionManager.kt`'s updated
  `resolve()`. A plain browser on the LAN can just be pointed at
  `https://192.168.1.147:8443` directly.

**Net effect:** at home, everything (chat, mic, push, PWA install) works over the LAN without
Tailscale or Twingate connected at all. Away from home, the app automatically falls back to
the `ai.dhaaselab.com` VPN domain exactly as before — nothing about the away-from-home path
changed.

## About Twingate/Tailscale's own "kill switch" (not code — an app setting)

There is no in-band kill switch left in this codebase (see `SECURITY_INCIDENT_RUNBOOK.md`'s
"Stop everything" section — the old file-flag emergency-stop tool was removed at Devin's
request in an earlier session). If something is still blocking all traffic when the VPN
client disconnects, that's Twingate's own **Internet Security** feature (an admin-console
setting, sometimes deployed as an "always-on"/full-tunnel mode via a Machine Key — see
https://www.twingate.com/docs/internet-security-client-configuration) or, if using a
router-level kill switch, that router's own "block non-VPN traffic" rule — neither lives in
this repo. To turn it off: Twingate Admin Console → the relevant Network's client
configuration → disable Internet Security / remove the enforced Machine Key so the desktop
client can be freely signed out or switched off; Tailscale itself doesn't ship a traffic
kill switch by default, so if traffic is being blocked there it's most likely a router-level
rule (e.g. a GL.iNet "block non-VPN traffic" toggle) rather than the Tailscale client itself.

# Play Console listing — copy/paste reference

Everything below is ready to paste into the Play Console once Devin has a developer
account. Written deliberately scoped to what the *app* does (a thin, authenticated client),
not what the agent behind it does — the spec's own reasoning: this keeps Google's review
evaluating a WebView client with a few native permissions, not trying to parse an
ambiguous "AI agent" description that invites more scrutiny than the app itself warrants.

## App details

**App name:** Jarvis

**Short description** (80 char max):
> Personal dashboard and remote-access client for a self-hosted assistant.

**Full description:**
> Jarvis is a client for a personal, self-hosted assistant that runs on hardware you own
> and control. This app connects to your own private server over your home network or a
> VPN you've already set up — it doesn't talk to any other service, and there's no public
> backend behind it.
>
> What it does:
> - Connects to your configured private server, trying your local network first and
>   falling back to your VPN address if you're away from home
> - Can wake a sleeping computer on your home network (Wake-on-LAN)
> - Supports voice input and sign-in flows your assistant's web interface already has
> - Delivers push notifications from your own server (approvals, alerts, and similar —
>   entirely configured by you, on your server)
>
> This app requires you to already have your own compatible self-hosted server running.
> It is not useful without one, and it does not include or provide that server.

**Category:** Tools (or Productivity — either fits; Tools is the closer match for "remote
client to something you host yourself")

**Contact email:** devinhaase90@gmail.com

**Privacy policy URL:** https://claude.ai/code/artifact/5a16f698-7875-4197-bc99-38dfcf8c8748
(published from this project — republish via the same conversation/file path if it ever
needs updating, or move it to a URL Devin controls directly if preferred long-term)

## Data safety section

Google's Data safety form asks about data *collected or shared*, not just accessed —
answer per data type below. Values may shift slightly as Google's own form wording changes
between now and submission; the underlying facts (what the app actually does) are these:

| Data type | Collected? | Shared with 3rd parties? | Purpose |
|---|---|---|---|
| Audio (microphone) | Yes, only while actively used | No — sent only to Devin's own configured server | App functionality (voice input) |
| Device/other IDs (FCM token) | Yes | Sent to Google Firebase Cloud Messaging (the delivery mechanism itself) and to Devin's own server | App functionality (push notifications) |
| App activity / in-app messages | Yes (chat/notification content) | No — stays between the device and Devin's own server | App functionality |
| Approximate or precise location | No | — | — |
| Personal identifiers (name, email, etc.) | No | — | — |
| Financial info | No | — | — |
| Photos/videos/files | No (the desktop/web client supports file attachments; this Android client does not add its own file-picker UI as built) | — | — |

**Data collection is not optional** for the core "voice input" and "notifications"
features specifically (both are opt-in permissions at the OS level, but the Data safety
form asks about the feature as designed, not per-user opt-out) — answer "required for app
functionality," not "optional," for audio and the FCM token.

**Encryption in transit:** Yes for both paths now (updated Sept 2026 — this used to be
plain HTTP on the local/LAN address, see git history for that era's wording). The VPN
address (`https://ai.dhaaselab.com`) uses a real publicly-trusted cert as before. The
local/LAN address now also uses HTTPS via a locally-issued cert (mkcert) trusted on-device
— see `REMOTE_ACCESS.md`'s "Local HTTPS on the LAN" section. `ConnectionManager.kt` tries
the local HTTPS address first, falling back to plain HTTP only if that cert isn't trusted
on this particular device yet (e.g. a fresh install before the one-time CA-trust step) —
so answer "yes, encrypted" here, but it's still worth noting in the form's free-text if it
allows nuance that the very first run on an unconfigured device could briefly fall back to
HTTP until the on-device setup step is done.

**Data deletion request mechanism:** Not applicable in the form's usual sense (no account
system, no server-side data retention Devin doesn't already fully control) — the contact
email above is the right thing to list if the form requires *something* here.

## Content rating questionnaire

Straightforward — no ads, no user-generated content shared with other users, no violence/
mature content of any kind, no in-app purchases, no user-to-user communication feature.
Should land in the lowest content rating tier (Google's rating bodies vary by region, the
questionnaire itself picks the right one from these answers).

## Store listing graphics still needed (Devin's own step)

Play Console requires actual screenshots (phone screenshots, minimum 2, and a feature
graphic 1024×500) that this agent can't capture without your real phone — the android_app
emulator screenshots taken while building this could work as a stand-in but are lower
resolution than Play typically wants for a polished listing. At minimum before submitting:
take 2-4 real screenshots of the app on your phone (chat view, Settings screen) and a
simple 1024×500 feature banner (could be as simple as the app icon centered on a blue
background matching the brand).

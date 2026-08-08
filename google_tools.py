"""
google_tools.py — Phase 6 item 4: Gmail (extended beyond Phase 1's read-only
read_recent_emails), Calendar, and Drive, all built on google_auth.py's encrypted,
narrow-scope credential store.

Tiers exactly as specified:
  Gmail:    search/read = Tier 1, draft = Tier 2, send = Tier 4 (always — see
            google_auth.py's docstring on why the OAuth scope alone doesn't enforce this,
            this Tier-4 gate is what actually does).
  Calendar: read = Tier 1, create/update = Tier 2, delete/move = Tier 4. The spec's own
            phrasing ("deleting or moving events it didn't create itself: Tier 3/4") is a
            range, and this project's tier system is static per-tool, not conditional on
            argument content — Tier 4 uniformly for delete/move is the documented, more
            cautious end of that range, a deliberate scope simplification, not a silent
            departure from it.
  Drive:    search/read = Tier 1, create = Tier 2, modify/delete existing = Tier 4.

Every function checks the specific scope it needs (not just "is anything connected") via
google_auth.connection_status() before attempting an API call, and degrades to a clear,
actionable error — "reconnect via google_auth_setup.py" — rather than a raw API exception,
same convention every other optional/external dependency in this codebase already follows
(nmap missing, msfconsole missing, Ollama down).
"""

import base64
from email.mime.text import MIMEText

import google_auth


def _gmail_service(require_compose: bool = False):
    """`require_compose` distinguishes readonly (search/read) from compose (draft/send) —
    a token can have one without the other, and checking only the coarser "gmail" flag let
    draft_email/send_email fall through to a raw 403 from the API instead of the same
    clean pre-check every other insufficient-scope case gets (found live-testing this)."""
    creds = google_auth.get_credentials()
    if creds is None:
        return None, {"error": "Google account not connected. Run google_auth_setup.py once to connect it."}
    status = google_auth.connection_status()
    if require_compose and not status["gmail_compose"]:
        return None, {"error": "Connected, but Gmail compose/send scope wasn't granted — reconnect via google_auth_setup.py."}
    if not require_compose and not status["gmail"]:
        return None, {"error": "Connected, but Gmail scope wasn't granted — reconnect via google_auth_setup.py."}
    from googleapiclient.discovery import build
    return build("gmail", "v1", credentials=creds), None


def _calendar_service():
    creds = google_auth.get_credentials()
    if creds is None:
        return None, {"error": "Google account not connected. Run google_auth_setup.py once to connect it."}
    status = google_auth.connection_status()
    if not status["calendar"]:
        return None, {"error": "Connected, but Calendar scope wasn't granted — reconnect via google_auth_setup.py."}
    from googleapiclient.discovery import build
    return build("calendar", "v3", credentials=creds), None


def _drive_service():
    creds = google_auth.get_credentials()
    if creds is None:
        return None, {"error": "Google account not connected. Run google_auth_setup.py once to connect it."}
    status = google_auth.connection_status()
    if not status["drive"]:
        return None, {"error": "Connected, but Drive scope wasn't granted — reconnect via google_auth_setup.py."}
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=creds), None


# ---------------------------------------------------------------------------
# Gmail — Tier 1: search/read. Tier 2: draft. Tier 4: send, always.
# ---------------------------------------------------------------------------

def search_emails(query: str, max_results: int = 10) -> dict:
    """Tier 1. `query` uses real Gmail search syntax (e.g. 'from:x is:unread')."""
    service, err = _gmail_service()
    if err:
        return err
    try:
        results = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
        messages = results.get("messages", [])
        summaries = []
        for msg in messages:
            full = service.users().messages().get(userId="me", id=msg["id"], format="metadata",
                                                    metadataHeaders=["Subject", "From", "Date"]).execute()
            headers = {h["name"]: h["value"] for h in full["payload"].get("headers", [])}
            summaries.append({
                "id": msg["id"], "subject": headers.get("Subject", "(no subject)"),
                "from": headers.get("From", "?"), "date": headers.get("Date", "?"),
                "snippet": full.get("snippet", ""),
            })
        return {"query": query, "count": len(summaries), "results": summaries}
    except Exception as e:
        return {"error": str(e)}


def read_email(message_id: str) -> dict:
    """Tier 1 — full body of one message, not just the metadata search_emails returns."""
    service, err = _gmail_service()
    if err:
        return err
    try:
        msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
        headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}

        def _extract_text(part):
            if part.get("mimeType") == "text/plain" and "data" in part.get("body", {}):
                return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
            for sub in part.get("parts", []):
                text = _extract_text(sub)
                if text:
                    return text
            return ""

        body = _extract_text(msg["payload"]) or msg.get("snippet", "")
        return {
            "id": message_id, "subject": headers.get("Subject", "(no subject)"),
            "from": headers.get("From", "?"), "date": headers.get("Date", "?"), "body": body[:5000],
        }
    except Exception as e:
        return {"error": str(e)}


def draft_email(to: str, subject: str, body: str) -> dict:
    """Tier 2 — creates a Gmail draft. Does NOT send it — see send_email, always Tier 4."""
    service, err = _gmail_service(require_compose=True)
    if err:
        return err
    try:
        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        draft = service.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
        return {"draft_id": draft["id"], "to": to, "subject": subject, "created": True}
    except Exception as e:
        return {"error": str(e)}


def send_email(to: str = None, subject: str = None, body: str = None, draft_id: str = None) -> dict:
    """Tier 4 — ALWAYS requires explicit confirmation, no exceptions. Either sends an
    existing draft (draft_id) or composes+sends directly (to/subject/body) — either path
    is gated the same way at the Coordinator level; this function doesn't get to choose
    which turns are worth asking about, every call does."""
    service, err = _gmail_service(require_compose=True)
    if err:
        return err
    try:
        if draft_id:
            result = service.users().drafts().send(userId="me", body={"id": draft_id}).execute()
        else:
            if not (to and subject and body):
                return {"error": "Provide either draft_id, or to/subject/body to compose and send directly."}
            message = MIMEText(body)
            message["to"] = to
            message["subject"] = subject
            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
            result = service.users().messages().send(userId="me", body={"raw": raw}).execute()
        return {"sent": True, "message_id": result.get("id")}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Calendar — Tier 1: read. Tier 2: create/update. Tier 4: delete/move.
# ---------------------------------------------------------------------------

def list_calendar_events(max_results: int = 10, time_min: str = None, calendar_id: str = "primary") -> dict:
    """Tier 1. `time_min` is an RFC3339 timestamp; defaults to now if not given."""
    service, err = _calendar_service()
    if err:
        return err
    try:
        import datetime
        time_min = time_min or datetime.datetime.utcnow().isoformat() + "Z"
        events = service.events().list(
            calendarId=calendar_id, timeMin=time_min, maxResults=max_results,
            singleEvents=True, orderBy="startTime",
        ).execute()
        items = [
            {"id": e["id"], "summary": e.get("summary", "(no title)"),
             "start": e.get("start", {}).get("dateTime", e.get("start", {}).get("date")),
             "end": e.get("end", {}).get("dateTime", e.get("end", {}).get("date"))}
            for e in events.get("items", [])
        ]
        return {"count": len(items), "events": items}
    except Exception as e:
        return {"error": str(e)}


def create_calendar_event(summary: str, start: str, end: str, description: str = "", calendar_id: str = "primary") -> dict:
    """Tier 2. `start`/`end` are RFC3339 timestamps (e.g. '2026-08-10T14:00:00-04:00')."""
    service, err = _calendar_service()
    if err:
        return err
    try:
        body = {
            "summary": summary, "description": description,
            "start": {"dateTime": start}, "end": {"dateTime": end},
        }
        created = service.events().insert(calendarId=calendar_id, body=body).execute()
        return {"event_id": created["id"], "summary": summary, "created": True, "link": created.get("htmlLink")}
    except Exception as e:
        return {"error": str(e)}


def update_calendar_event(event_id: str, summary: str = None, start: str = None, end: str = None,
                           description: str = None, calendar_id: str = "primary") -> dict:
    """Tier 2."""
    service, err = _calendar_service()
    if err:
        return err
    try:
        event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
        if summary is not None:
            event["summary"] = summary
        if description is not None:
            event["description"] = description
        if start is not None:
            event["start"] = {"dateTime": start}
        if end is not None:
            event["end"] = {"dateTime": end}
        updated = service.events().update(calendarId=calendar_id, eventId=event_id, body=event).execute()
        return {"event_id": event_id, "updated": True, "summary": updated.get("summary")}
    except Exception as e:
        return {"error": str(e)}


def delete_calendar_event(event_id: str, calendar_id: str = "primary") -> dict:
    """Tier 4 — see module docstring on why this is uniformly Tier 4 rather than
    conditional on who created the event."""
    service, err = _calendar_service()
    if err:
        return err
    try:
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        return {"event_id": event_id, "deleted": True}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Drive — Tier 1: search/read. Tier 2: create. Tier 4: modify/delete existing.
# ---------------------------------------------------------------------------

def search_drive_files(query: str, max_results: int = 10) -> dict:
    """Tier 1. drive.file scope means this only ever sees files this app created or Devin
    explicitly opened with it — not a blanket Drive search, by design."""
    service, err = _drive_service()
    if err:
        return err
    try:
        results = service.files().list(
            q=query, pageSize=max_results, fields="files(id, name, mimeType, modifiedTime)",
        ).execute()
        return {"query": query, "files": results.get("files", [])}
    except Exception as e:
        return {"error": str(e)}


def read_drive_file(file_id: str, max_chars: int = 5000) -> dict:
    """Tier 1."""
    service, err = _drive_service()
    if err:
        return err
    try:
        meta = service.files().get(fileId=file_id, fields="name, mimeType").execute()
        content = service.files().get_media(fileId=file_id).execute()
        text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else str(content)
        return {"file_id": file_id, "name": meta.get("name"), "content": text[:max_chars]}
    except Exception as e:
        return {"error": str(e)}


def create_drive_file(name: str, content: str, mime_type: str = "text/plain") -> dict:
    """Tier 2."""
    service, err = _drive_service()
    if err:
        return err
    try:
        import io
        from googleapiclient.http import MediaIoBaseUpload
        media = MediaIoBaseUpload(io.BytesIO(content.encode("utf-8")), mimetype=mime_type)
        created = service.files().create(body={"name": name}, media_body=media, fields="id, name").execute()
        return {"file_id": created["id"], "name": created["name"], "created": True}
    except Exception as e:
        return {"error": str(e)}


def update_drive_file(file_id: str, content: str, mime_type: str = "text/plain") -> dict:
    """Tier 4 — modifying an EXISTING file's content, same "never touch existing content
    without confirmation" posture obsidian_tools.py's overwrite_note already applies."""
    service, err = _drive_service()
    if err:
        return err
    try:
        import io
        from googleapiclient.http import MediaIoBaseUpload
        media = MediaIoBaseUpload(io.BytesIO(content.encode("utf-8")), mimetype=mime_type)
        updated = service.files().update(fileId=file_id, media_body=media, fields="id, name").execute()
        return {"file_id": updated["id"], "name": updated["name"], "updated": True}
    except Exception as e:
        return {"error": str(e)}


def delete_drive_file(file_id: str) -> dict:
    """Tier 4."""
    service, err = _drive_service()
    if err:
        return err
    try:
        service.files().delete(fileId=file_id).execute()
        return {"file_id": file_id, "deleted": True}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Connection status — Tier 1, straight passthrough for the dashboard (Phase 6 item 5).
# ---------------------------------------------------------------------------

def google_connection_status() -> dict:
    return google_auth.connection_status()

"""
Tests for Phase 6 item 4: Google integration (google_auth.py's encrypted credential
store, google_tools.py's Gmail/Calendar/Drive functions).

Uses mocked Credentials/API-client objects throughout — never touches Devin's real
Google account or the real data/google_token.enc. The real thing was already live-tested
directly while building this (see task.md): the real (Phase 1, readonly-only) token
migrated into the new encrypted store, a real search_emails() call against real Gmail,
and real graceful-degradation checks for Calendar/Drive/Gmail-compose (none of which are
granted on that older token) — including two real bugs found and fixed during that live
testing, each with a dedicated regression test below.

Run: python test_google_phase6.py
"""

import os
import sys
import json
import shutil
import tempfile

PASS = 0
FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}  {detail}")


def section(title):
    print(f"\n=== {title} ===")


import google_auth

_real_token_path = google_auth.TOKEN_PATH
_tmp_dir = tempfile.mkdtemp()
google_auth.TOKEN_PATH = os.path.join(_tmp_dir, "google_token.enc")
google_auth.DATA_DIR = _tmp_dir


class _FakeCreds:
    """Minimal stand-in for google.oauth2.credentials.Credentials — just enough surface
    for save_credentials()/load_credentials() to round-trip it."""
    def __init__(self, scopes, expired=False, refresh_token="rt", valid=True):
        self.scopes = scopes
        self.expired = expired
        self.refresh_token = refresh_token
        self._valid = valid
        self.token = "at"
        self.refresh_token_val = refresh_token
        self.client_id = "cid"
        self.client_secret = "csecret"
        self.token_uri = "https://oauth2.googleapis.com/token"

    @property
    def valid(self):
        return self._valid and not self.expired

    def to_json(self):
        import datetime
        # A real google.oauth2.credentials.Credentials reconstructed with no `expiry` at
        # all is treated as expired (forcing a refresh attempt) — found live-testing this,
        # not assumed. Give the fake a real future expiry so reload doesn't try to refresh
        # against a nonexistent OAuth client, which is what this section is testing
        # (successful decrypt+reconstruct), not the separate refresh path.
        future = (datetime.datetime.utcnow() + datetime.timedelta(hours=1)).isoformat() + "Z"
        return json.dumps({
            "token": self.token, "refresh_token": self.refresh_token,
            "client_id": self.client_id, "client_secret": self.client_secret,
            "token_uri": self.token_uri, "scopes": self.scopes, "expiry": future,
        })


try:
    section("1. Encryption round-trip and key persistence across a fresh process")

    key1 = google_auth._get_or_create_key()
    check("a key is generated on first use", bool(key1))

    # Simulate a fresh process: drop it from os.environ, re-read .env from disk.
    os.environ.pop("GOOGLE_TOKEN_ENCRYPTION_KEY", None)
    from dotenv import load_dotenv
    load_dotenv(override=True)
    key2 = google_auth._get_or_create_key()
    check("the SAME key persists across a simulated fresh process (read from .env, not regenerated)",
          key1 == key2, f"got different keys: {key1!r} vs {key2!r}")

    fake_creds = _FakeCreds(scopes=["https://www.googleapis.com/auth/gmail.readonly"])
    google_auth.save_credentials(fake_creds)
    check("save_credentials writes an encrypted (non-plaintext) file",
          os.path.exists(google_auth.TOKEN_PATH) and b"gmail.readonly" not in open(google_auth.TOKEN_PATH, "rb").read())

    loaded = google_auth.load_credentials()
    check("load_credentials decrypts and reconstructs valid credentials", loaded is not None and loaded.valid, f"got: {loaded}")
    check("reconstructed credentials carry the real stored scopes", loaded.scopes == ["https://www.googleapis.com/auth/gmail.readonly"], f"got: {loaded.scopes if loaded else None}")

    section("2. Regression: a token's own scopes are used on reload, never overridden by")
    section("   this module's current SCOPES wishlist (the real bug found live)")

    # A token that only ever had readonly granted must NOT be silently "upgraded" to also
    # claim calendar/drive on reload just because google_auth.SCOPES now lists them.
    with open(google_auth.TOKEN_PATH, "rb") as f:
        encrypted = f.read()
    stored_plain = json.loads(google_auth._fernet().decrypt(encrypted))
    check("the stored token's own scopes list is exactly what was saved, not the full current SCOPES",
          stored_plain["scopes"] == ["https://www.googleapis.com/auth/gmail.readonly"],
          f"got: {stored_plain['scopes']} vs full SCOPES {google_auth.SCOPES}")

    status = google_auth.connection_status()
    check("connection_status reports gmail=True (readonly granted)", status["gmail"] is True)
    check("connection_status reports gmail_compose=False (never granted)", status["gmail_compose"] is False, f"got: {status}")
    check("connection_status reports calendar=False (never granted)", status["calendar"] is False, f"got: {status}")
    check("connection_status reports drive=False (never granted)", status["drive"] is False, f"got: {status}")

    section("3. connection_status() with nothing connected at all")
    os.remove(google_auth.TOKEN_PATH)
    empty_status = google_auth.connection_status()
    check("no token at all -> connected=False and every service False",
          empty_status == {"connected": False, "gmail": False, "calendar": False, "drive": False}, f"got: {empty_status}")

finally:
    google_auth.TOKEN_PATH = _real_token_path
    google_auth.DATA_DIR = "data"
    shutil.rmtree(_tmp_dir, ignore_errors=True)

# ---------------------------------------------------------------------------
section("4. google_tools.py — graceful degradation when not connected / missing scope")
# ---------------------------------------------------------------------------

import google_tools as gt

_real_get_credentials = google_auth.get_credentials
_real_connection_status = google_auth.connection_status


def _set_fake_connection(connected, **scope_flags):
    if not connected:
        google_auth.get_credentials = lambda: None
        google_auth.connection_status = lambda: {"connected": False, "gmail": False, "gmail_compose": False, "calendar": False, "drive": False}
    else:
        google_auth.get_credentials = lambda: _FakeCreds(scopes=[])
        status = {"connected": True, "gmail": False, "gmail_compose": False, "calendar": False, "drive": False}
        status.update(scope_flags)
        google_auth.connection_status = lambda: status


try:
    _set_fake_connection(False)
    for fn, args in [
        (gt.search_emails, ("query",)), (gt.read_email, ("id",)), (gt.draft_email, ("a", "b", "c")),
        (gt.send_email, ()), (gt.list_calendar_events, ()), (gt.create_calendar_event, ("s", "start", "end")),
        (gt.search_drive_files, ("q",)), (gt.create_drive_file, ("n", "c")),
    ]:
        result = fn(*args)
        check(f"{fn.__name__} refuses cleanly when Google isn't connected at all", "error" in result and "not connected" in result["error"], f"got: {result}")

    _set_fake_connection(True, gmail=True, gmail_compose=False)
    # search_emails/read_email against a real (mocked) service are exercised in section 5
    # below — this section is specifically about the scope-refusal paths.
    draft_result = gt.draft_email("a", "b", "c")
    check("draft_email refuses cleanly when connected but compose scope isn't granted (the real bug found live)",
          "error" in draft_result and "compose" in draft_result["error"], f"got: {draft_result}")
    send_result = gt.send_email(to="a", subject="b", body="c")
    check("send_email refuses cleanly when connected but compose scope isn't granted",
          "error" in send_result and "compose" in send_result["error"], f"got: {send_result}")

    _set_fake_connection(True, gmail=True, gmail_compose=True, calendar=False, drive=False)
    cal_result = gt.list_calendar_events()
    check("list_calendar_events refuses cleanly when Calendar scope isn't granted", "error" in cal_result and "Calendar" in cal_result["error"], f"got: {cal_result}")
    drive_result = gt.search_drive_files("q")
    check("search_drive_files refuses cleanly when Drive scope isn't granted", "error" in drive_result and "Drive" in drive_result["error"], f"got: {drive_result}")
finally:
    google_auth.get_credentials = _real_get_credentials
    google_auth.connection_status = _real_connection_status

# ---------------------------------------------------------------------------
section("5. google_tools.py — successful paths against a mocked API client")
# ---------------------------------------------------------------------------


class _FakeExec:
    def __init__(self, value):
        self._value = value

    def execute(self):
        return self._value


class _FakeGmailMessages:
    def list(self, **kwargs):
        return _FakeExec({"messages": [{"id": "m1"}]})

    def get(self, **kwargs):
        return _FakeExec({
            "payload": {"headers": [{"name": "Subject", "value": "Test Subject"}, {"name": "From", "value": "a@b.com"}]},
            "snippet": "a snippet",
        })

    def send(self, **kwargs):
        return _FakeExec({"id": "sent1"})


class _FakeGmailDrafts:
    def create(self, **kwargs):
        return _FakeExec({"id": "draft1"})

    def send(self, **kwargs):
        return _FakeExec({"id": "sent2"})


class _FakeGmailUsers:
    def messages(self):
        return _FakeGmailMessages()

    def drafts(self):
        return _FakeGmailDrafts()


class _FakeGmailService:
    def users(self):
        return _FakeGmailUsers()


class _FakeCalendarEvents:
    def list(self, **kwargs):
        return _FakeExec({"items": [{"id": "e1", "summary": "Meeting", "start": {"dateTime": "2026-01-01T10:00:00"}, "end": {"dateTime": "2026-01-01T11:00:00"}}]})

    def insert(self, **kwargs):
        return _FakeExec({"id": "e2", "htmlLink": "http://example.com/e2"})

    def get(self, **kwargs):
        return _FakeExec({"id": "e1", "summary": "Old Summary"})

    def update(self, **kwargs):
        return _FakeExec({"id": "e1", "summary": kwargs["body"]["summary"]})

    def delete(self, **kwargs):
        return _FakeExec({})


class _FakeCalendarService:
    def events(self):
        return _FakeCalendarEvents()


class _FakeDriveFiles:
    def list(self, **kwargs):
        return _FakeExec({"files": [{"id": "f1", "name": "doc.txt"}]})

    def get(self, **kwargs):
        return _FakeExec({"name": "doc.txt", "mimeType": "text/plain"})

    def get_media(self, **kwargs):
        # Real Drive API: get_media() also returns a request object with .execute(), same
        # shape as every other call here — my first draft of this mock returned raw bytes
        # directly, which doesn't match the real client and broke the test, not the code
        # under test (read_drive_file itself calls .execute() correctly).
        return _FakeExec(b"file content")

    def create(self, **kwargs):
        return _FakeExec({"id": "f2", "name": kwargs["body"]["name"]})

    def update(self, **kwargs):
        return _FakeExec({"id": kwargs["fileId"], "name": "updated.txt"})

    def delete(self, **kwargs):
        return _FakeExec({})


class _FakeDriveService:
    def files(self):
        return _FakeDriveFiles()


_real_gmail_svc, _real_cal_svc, _real_drive_svc = gt._gmail_service, gt._calendar_service, gt._drive_service
try:
    gt._gmail_service = lambda require_compose=False: (_FakeGmailService(), None)
    result = gt.search_emails("test query")
    check("search_emails returns real-shaped results against a mocked service", result.get("count") == 1 and result["results"][0]["subject"] == "Test Subject", f"got: {result}")

    read_result = gt.read_email("m1")
    check("read_email works against a mocked service", "error" not in read_result, f"got: {read_result}")

    draft_result = gt.draft_email("a@b.com", "subj", "body")
    check("draft_email creates a draft, doesn't send", draft_result.get("created") is True and "draft_id" in draft_result, f"got: {draft_result}")

    send_result = gt.send_email(draft_id="draft1")
    check("send_email sends an existing draft", send_result.get("sent") is True, f"got: {send_result}")

    send_direct = gt.send_email(to="a@b.com", subject="s", body="b")
    check("send_email also supports composing+sending directly", send_direct.get("sent") is True, f"got: {send_direct}")

    incomplete_send = gt.send_email()
    check("send_email with neither draft_id nor to/subject/body returns a clean error", "error" in incomplete_send, f"got: {incomplete_send}")

    gt._calendar_service = lambda: (_FakeCalendarService(), None)
    events = gt.list_calendar_events()
    check("list_calendar_events works against a mocked service", events.get("count") == 1, f"got: {events}")

    created = gt.create_calendar_event("New Meeting", "2026-01-01T10:00:00", "2026-01-01T11:00:00")
    check("create_calendar_event works against a mocked service", created.get("created") is True, f"got: {created}")

    updated = gt.update_calendar_event("e1", summary="Updated Meeting")
    check("update_calendar_event works against a mocked service", updated.get("updated") is True, f"got: {updated}")

    deleted = gt.delete_calendar_event("e1")
    check("delete_calendar_event works against a mocked service", deleted.get("deleted") is True, f"got: {deleted}")

    gt._drive_service = lambda: (_FakeDriveService(), None)
    files = gt.search_drive_files("test")
    check("search_drive_files works against a mocked service", len(files.get("files", [])) == 1, f"got: {files}")

    file_content = gt.read_drive_file("f1")
    check("read_drive_file works against a mocked service", file_content.get("content") == "file content", f"got: {file_content}")

    created_file = gt.create_drive_file("new.txt", "content")
    check("create_drive_file works against a mocked service", created_file.get("created") is True, f"got: {created_file}")

    updated_file = gt.update_drive_file("f1", "new content")
    check("update_drive_file works against a mocked service", updated_file.get("updated") is True, f"got: {updated_file}")

    deleted_file = gt.delete_drive_file("f1")
    check("delete_drive_file works against a mocked service", deleted_file.get("deleted") is True, f"got: {deleted_file}")
finally:
    gt._gmail_service, gt._calendar_service, gt._drive_service = _real_gmail_svc, _real_cal_svc, _real_drive_svc

# ---------------------------------------------------------------------------
section("6. Tools registered in ALL_TOOLS with the specified tiers")
# ---------------------------------------------------------------------------

from tools import ALL_TOOLS, Tier

expected = {
    "search_emails": Tier.TIER_1, "read_email": Tier.TIER_1, "draft_email": Tier.TIER_2, "send_email": Tier.TIER_4,
    "list_calendar_events": Tier.TIER_1, "create_calendar_event": Tier.TIER_2, "update_calendar_event": Tier.TIER_2,
    "delete_calendar_event": Tier.TIER_4, "search_drive_files": Tier.TIER_1, "read_drive_file": Tier.TIER_1,
    "create_drive_file": Tier.TIER_2, "update_drive_file": Tier.TIER_4, "delete_drive_file": Tier.TIER_4,
}
for name, tier in expected.items():
    check(f"'{name}' registered at {tier.name}", name in ALL_TOOLS and ALL_TOOLS[name].tier == tier, f"got: {ALL_TOOLS.get(name)}")

check("send_email is Tier 4 regardless of draft-vs-direct path (one tool, one tier, always gated)",
      ALL_TOOLS["send_email"].tier == Tier.TIER_4)
check("google_connection_status is registered and Tier 1", "google_connection_status" in ALL_TOOLS and ALL_TOOLS["google_connection_status"].tier == Tier.TIER_1)


print(f"\n{'=' * 55}\n{PASS} passed, {FAIL} failed\n{'=' * 55}")
sys.exit(1 if FAIL else 0)

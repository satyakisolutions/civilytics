# backend/tests/test_client_log_ingest.py
"""Browser console-log ingest: caps, redaction, and trust boundary.

Everything here arrives from a browser, so the tests are mostly about what
a hostile or buggy client cannot do.
"""
import gzip
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.logs import client_ingest
from app.logs.client_ingest import (
    ClientLogError,
    build_rows,
    parse_envelope,
)

VIEWER = {
    "sub": "viewer-sub",
    "preferred_username": "operator7",
    "ww_tenant": "tenant-a",
    "realm_access": {"roles": ["viewer"]},
}


def _env(records, **over):
    envelope = {
        "session_id": "sess-abc",
        "url": "https://civilytics.example/",
        "user_agent": "Mozilla/5.0 (Linux; Android 13)",
        "records": records,
        "transport": "ws",
    }
    envelope.update(over)
    return gzip.compress(json.dumps(envelope).encode())


def _rec(message, level="log", **over):
    rec = {"level": level, "message": message, "ts": 1762000000000}
    rec.update(over)
    return rec


# ── gzip envelope handling ─────────────────────────────────────────────────

def test_round_trip():
    rows = build_rows(parse_envelope(_env([_rec("hello")])), claims=VIEWER)
    assert len(rows) == 1
    assert rows[0]["message"] == "hello"
    assert rows[0]["service"] == "browser"


def test_non_gzip_rejected():
    with pytest.raises(ClientLogError, match="gzip"):
        parse_envelope(b"not gzip at all")


def test_gzip_of_non_json_rejected():
    with pytest.raises(ClientLogError, match="JSON"):
        parse_envelope(gzip.compress(b"<html>nope</html>"))


def test_oversized_compressed_payload_rejected():
    blob = b"x" * (client_ingest.MAX_COMPRESSED_BYTES + 1)
    with pytest.raises(ClientLogError, match="too large"):
        parse_envelope(blob)


def test_zip_bomb_rejected_without_allocating_it():
    """A small gzip that expands past the ceiling must not be decompressed.

    gzip.decompress() would happily allocate all of this; the bounded
    decompressobj is the reason it cannot.
    """
    bomb = gzip.compress(b"\\0" * (client_ingest.MAX_DECOMPRESSED_BYTES * 4))
    assert len(bomb) < client_ingest.MAX_COMPRESSED_BYTES  # small on the wire
    with pytest.raises(ClientLogError, match="too large"):
        parse_envelope(bomb)


def test_envelope_must_be_an_object():
    with pytest.raises(ClientLogError, match="object"):
        parse_envelope(gzip.compress(b'["not", "an", "object"]'))


def test_records_must_be_a_list():
    with pytest.raises(ClientLogError, match="records"):
        build_rows({"records": "nope"}, claims=VIEWER)


def test_batch_size_capped():
    too_many = [_rec(f"line {i}") for i in range(client_ingest.MAX_RECORDS_PER_BATCH + 1)]
    with pytest.raises(ClientLogError, match="too many"):
        build_rows({"records": too_many}, claims=VIEWER)


# ── redaction ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("secret_line", [
    'fetch failed {"token": "eyJhbGciOiJSUzI1NiJ9.abc.def"}',
    "Authorization: Bearer eyJhbGciOiJSUzI1NiJ9.abc.def",
    'login body {"username": "bob", "password": "hunter2"}',
    "ww_access=eyJhbGciOiJSUzI1NiJ9.abc.def",
])
def test_credentials_redacted_server_side(secret_line):
    """The shim redacts first, but a client that skipped it must not be able
    to persist a live bearer token. /auth/ws-token returns one, so a single
    console.log of a failed fetch is enough to leak it."""
    rows = build_rows(parse_envelope(_env([_rec(secret_line)])), claims=VIEWER)
    stored = rows[0]["message"]
    assert "hunter2" not in stored
    assert "eyJhbGciOiJSUzI1NiJ9.abc.def" not in stored
    assert "REDACTED" in stored


def test_stack_and_url_also_redacted():
    rows = build_rows(
        parse_envelope(_env(
            [_rec("boom", stack="at f (https://x/?token=eyJabcdefghij)")],
            url="https://x/cb?access_token=eyJabcdefghij",
        )),
        claims=VIEWER,
    )
    assert "eyJabcdefghij" not in rows[0]["meta"]["stack"]
    assert "eyJabcdefghij" not in rows[0]["meta"]["url"]


# ── trust boundary: identity comes from the token ──────────────────────────

def test_identity_taken_from_token_not_envelope():
    """A browser claiming to be someone else must not be believed."""
    rows = build_rows(
        parse_envelope(_env([_rec("x")], username="admin", tenant="tenant-b")),
        claims=VIEWER,
    )
    meta = rows[0]["meta"]
    assert meta["username"] == "operator7"
    assert meta["tenant"] == "tenant-a"
    assert meta["roles"] == ["viewer"]


def test_unknown_level_coerced():
    rows = build_rows(parse_envelope(_env([_rec("x", level="CRITICAL-ish")])), claims=VIEWER)
    assert rows[0]["level"] == "log"


def test_long_message_clipped():
    rows = build_rows(parse_envelope(_env([_rec("y" * 99_999)])), claims=VIEWER)
    assert len(rows[0]["message"]) <= client_ingest.MAX_MESSAGE_CHARS


def test_non_dict_records_skipped_not_fatal():
    rows = build_rows({"records": ["a string", _rec("real")]}, claims=VIEWER)
    assert [r["message"] for r in rows] == ["real"]


# ── client clocks ──────────────────────────────────────────────────────────

def test_absurd_client_timestamp_replaced_with_server_time():
    """A browser with a wrong clock would otherwise file its logs in 1970
    and disappear from every time-ranged query."""
    rows = build_rows(parse_envelope(_env([_rec("x", ts=1)])), claims=VIEWER)
    assert rows[0]["timestamp"] > datetime.now(timezone.utc) - timedelta(minutes=5)


def test_plausible_client_timestamp_kept():
    ts = datetime.now(timezone.utc) - timedelta(minutes=10)
    rows = build_rows(
        parse_envelope(_env([_rec("x", ts=ts.timestamp() * 1000)])), claims=VIEWER
    )
    assert abs((rows[0]["timestamp"] - ts).total_seconds()) < 2


def test_missing_timestamp_defaults_to_now():
    rows = build_rows({"records": [{"level": "warn", "message": "x"}]}, claims=VIEWER)
    assert rows[0]["timestamp"] <= datetime.now(timezone.utc)


# ── dropped-line accounting ────────────────────────────────────────────────

def test_client_drop_count_becomes_a_row():
    """A gap in the stream should be visible, not silent."""
    rows = build_rows(parse_envelope(_env([_rec("x")], dropped=42)), claims=VIEWER)
    assert len(rows) == 2
    assert rows[-1]["level"] == "warn"
    assert "42" in rows[-1]["message"]


def test_no_drop_row_when_nothing_dropped():
    rows = build_rows(parse_envelope(_env([_rec("x")])), claims=VIEWER)
    assert len(rows) == 1

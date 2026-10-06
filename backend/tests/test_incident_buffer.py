"""IncidentBuffer: bounded capture, redaction, and flush."""
import json
import logging
import time

import pytest

from app.logs.incident_buffer import (
    IncidentBuffer,
    IncidentBufferConfig,
    redact,
)


@pytest.fixture
def buf(tmp_path):
    cfg = IncidentBufferConfig(output_dir=tmp_path / "incidents")
    b = IncidentBuffer(cfg)
    b.setFormatter(logging.Formatter("%(message)s"))
    yield b
    b.close()


def _log(buf, msg, level=logging.DEBUG, name="test"):
    rec = logging.LogRecord(name, level, "f.py", 1, msg, None, None)
    rec.module = "f"
    buf.emit(rec)
    return rec


# ── redaction ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,leaked", [
    ('{"username": "bob", "password": "hunter2"}', "hunter2"),
    ("password=hunter2", "hunter2"),
    ("client_secret: abc123xyz", "abc123xyz"),
    ('{"access_token": "eyJhbGciOi"}', "eyJhbGciOi"),
    ("Authorization: Bearer eyJhbGciOiJSUzI1NiJ9.abc", "eyJhbGciOiJSUzI1NiJ9.abc"),
    ("Cookie: ww_access=sometoken", "sometoken"),
    ("bot_token=123456:AAEhBOweik", "AAEhBOweik"),
])
def test_redacts_credentials(text, leaked):
    out = redact(text)
    assert leaked not in out, f"leaked {leaked!r} in {out!r}"
    assert "REDACTED" in out


def test_redaction_keeps_innocent_text():
    out = redact("camera cam-01 detected 3 objects")
    assert out == "camera cam-01 detected 3 objects"


def test_redaction_applied_at_capture_not_flush(buf):
    """Plaintext must never sit in process memory waiting for a flush."""
    _log(buf, 'login payload {"password": "hunter2"}')
    assert all("hunter2" not in r.message for r in buf.snapshot())


# ── bounding ──────────────────────────────────────────────────────────
def test_count_bound_drops_oldest(tmp_path):
    b = IncidentBuffer(IncidentBufferConfig(max_records=5, output_dir=tmp_path))
    for i in range(20):
        _log(b, f"msg{i}")
    snap = b.snapshot()
    assert len(snap) == 5
    assert snap[-1].message == "msg19"          # newest kept
    assert b.stats()["dropped"] == 15


def test_age_bound_evicts_old_records(tmp_path):
    b = IncidentBuffer(IncidentBufferConfig(max_age_s=1.0, output_dir=tmp_path))
    old = logging.LogRecord("t", logging.DEBUG, "f.py", 1, "ancient", None, None)
    old.module = "f"
    old.created = time.time() - 600          # well outside the window
    b.emit(old)
    _log(b, "recent")                        # append triggers eviction
    msgs = [r.message for r in b.snapshot()]
    assert "ancient" not in msgs
    assert "recent" in msgs


# ── flush ─────────────────────────────────────────────────────────────
def test_flush_writes_header_and_records(buf, tmp_path):
    _log(buf, "approach detected")
    _log(buf, "violation fired", level=logging.WARNING)
    out = buf.flush_incident("cam-01_track42", context={"camera": "cam-01"})
    assert out is not None and out.exists()

    lines = out.read_text().strip().split("\n")
    header = json.loads(lines[0])
    assert header["incident_id"] == "cam-01_track42"
    assert header["record_count"] == 2
    assert header["context"] == {"camera": "cam-01"}
    assert [json.loads(l)["message"] for l in lines[1:]] == [
        "approach detected", "violation fired",
    ]


def test_flush_redacts_context(buf):
    _log(buf, "something to flush")   # flush returns None on an empty ring
    out = buf.flush_incident("x", context={"token": "secret-value"})
    header = json.loads(out.read_text().split("\n")[0])
    assert "secret-value" not in json.dumps(header)


def test_flush_sanitises_incident_id(buf):
    _log(buf, "x")
    out = buf.flush_incident("../../etc/passwd")
    assert out is not None
    assert "/etc/passwd" not in str(out)
    assert out.parent == buf._config.output_dir


def test_flush_with_empty_buffer_returns_none(buf):
    assert buf.flush_incident("nothing-here") is None


def test_flush_does_not_clear_buffer(buf):
    """Two incidents seconds apart must both get their context."""
    _log(buf, "shared context")
    buf.flush_incident("first")
    assert len(buf.snapshot()) == 1
    assert buf.flush_incident("second") is not None


def test_prune_keeps_only_max_incidents(tmp_path):
    b = IncidentBuffer(IncidentBufferConfig(
        output_dir=tmp_path / "inc", max_incidents_on_disk=3,
    ))
    for i in range(8):
        _log(b, f"event {i}")
        b.flush_incident(f"inc{i}")
    assert len(list((tmp_path / "inc").glob("*.jsonl"))) == 3


# ── robustness ────────────────────────────────────────────────────────
def test_emit_never_raises_on_bad_record(buf):
    """A handler that raises breaks the call site that was only logging."""
    bad = logging.LogRecord("t", logging.DEBUG, "f.py", 1, "%d", ("not-an-int",), None)
    bad.module = "f"
    buf.emit(bad)        # must not raise


def test_lookback_window_filters(buf):
    old = logging.LogRecord("t", logging.DEBUG, "f.py", 1, "old", None, None)
    old.module = "f"
    old.created = time.time() - 120
    buf.emit(old)
    _log(buf, "new")
    recent = [r.message for r in buf.snapshot(lookback_s=10)]
    assert recent == ["new"]

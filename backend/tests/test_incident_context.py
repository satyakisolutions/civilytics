# backend/tests/test_incident_context.py
"""Incident snapshot: MediaMTX stream state + whitelisted Redis keys.

The snapshot runs while something is already broken, so the tests are
mostly about what happens when its own dependencies are the broken thing.
"""
import asyncio
import logging

import httpx
import pytest

from app.logs import incident_context
from app.logs.incident_buffer import IncidentBuffer, IncidentBufferConfig

PATHS_PAYLOAD = {
    "items": [
        {
            "name": "cam-01",
            "ready": True,
            "source": {"type": "rtspSession"},
            "tracks": ["H264"],
            "bytesReceived": 123456,
            "bytesSent": 99,
            "readers": [{"type": "webRTCSession"}, {"type": "webRTCSession"}],
        },
        {
            "name": "cam-02",
            "ready": True,
            "source": {"type": "rtspSession"},
            "tracks": ["H264"],
            "bytesReceived": 222,
            "bytesSent": 0,
            "readers": [],
        },
        {"name": "cam-03", "ready": False, "source": None, "readers": []},
    ]
}


class FakeRedis:
    def __init__(self, data=None, *, broken=False):
        self.data = data or {}
        self.broken = broken

    async def scan_iter(self, match=None, count=None):
        if self.broken:
            raise ConnectionError("redis down")
        import fnmatch
        for key in list(self.data):
            if match is None or fnmatch.fnmatch(key, match):
                yield key

    async def type(self, key):
        return "string"

    async def get(self, key):
        return self.data.get(key)

    async def aclose(self):
        pass


def _patch_mediamtx(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient

    def factory(*a, **kw):
        kw["transport"] = transport
        return real(*a, **kw)

    monkeypatch.setattr(incident_context.httpx, "AsyncClient", factory)


def _patch_redis(monkeypatch, fake):
    monkeypatch.setattr(incident_context.redis, "from_url", lambda *a, **kw: fake)


# ── MediaMTX: online inputs and outputs ────────────────────────────────────

async def test_mediamtx_input_and_output_counts(monkeypatch):
    _patch_mediamtx(monkeypatch, lambda req: httpx.Response(200, json=PATHS_PAYLOAD))
    out = await incident_context.collect_mediamtx()
    assert out["reachable"] is True
    assert out["path_count"] == 3
    assert out["inputs_online"] == 2
    assert out["inputs_offline"] == 1
    cam01 = next(p for p in out["paths"] if p["name"] == "cam-01")
    assert cam01["reader_count"] == 2
    assert cam01["reader_types"] == ["webRTCSession"]


async def test_publishing_with_no_readers_is_called_out(monkeypatch):
    """A ready path with zero readers is a different fault from a dead path,
    and it is the one that is easy to miss."""
    _patch_mediamtx(monkeypatch, lambda req: httpx.Response(200, json=PATHS_PAYLOAD))
    out = await incident_context.collect_mediamtx()
    assert out["ready_without_readers"] == ["cam-02"]


async def test_mediamtx_unreachable_is_recorded_not_raised(monkeypatch):
    """"MediaMTX unreachable" is often the entire diagnosis."""
    def boom(req):
        raise httpx.ConnectError("refused")
    _patch_mediamtx(monkeypatch, boom)
    out = await incident_context.collect_mediamtx()
    assert out["reachable"] is False
    assert "ConnectError" in out["error"]
    assert out["paths"] == []


async def test_mediamtx_http_error_is_recorded(monkeypatch):
    _patch_mediamtx(monkeypatch, lambda req: httpx.Response(500, text="nope"))
    out = await incident_context.collect_mediamtx()
    assert out["reachable"] is False


# ── Redis: whitelisted keys only ───────────────────────────────────────────

async def test_only_whitelisted_keys_are_captured(monkeypatch):
    _patch_redis(monkeypatch, FakeRedis({
        "ww:pipeline:status": "running",
        "ww:pipeline:fps": "14.9",
        "ww:camera:abc:fps": "15",
        "ww:health:overall": "ok",
        # Must NOT appear: outside every whitelist pattern.
        "ww:secret:client_token": "eyJhbGciOiJSUzI1NiJ9.abc.def",
        "some:other:key": "x",
    }))
    out = await incident_context.collect_redis()
    assert out["reachable"] is True
    assert "ww:pipeline:status" in out["keys"]
    assert "ww:camera:abc:fps" in out["keys"]
    assert "ww:secret:client_token" not in out["keys"]
    assert "some:other:key" not in out["keys"]


async def test_values_are_redacted(monkeypatch):
    """The snapshot is written to a file that leaves the host, so even a
    whitelisted key gets the redactor."""
    _patch_redis(monkeypatch, FakeRedis({
        "ww:pipeline:status": 'running token="eyJhbGciOiJSUzI1NiJ9.abc.def"',
    }))
    out = await incident_context.collect_redis()
    assert "eyJhbGciOiJSUzI1NiJ9.abc.def" not in out["keys"]["ww:pipeline:status"]
    assert "REDACTED" in out["keys"]["ww:pipeline:status"]


async def test_key_count_capped(monkeypatch):
    many = {f"ww:health:c{i}": "ok" for i in range(incident_context.MAX_KEYS + 50)}
    _patch_redis(monkeypatch, FakeRedis(many))
    out = await incident_context.collect_redis()
    assert out["key_count"] == incident_context.MAX_KEYS
    assert out["truncated"] is True


async def test_redis_down_is_recorded_not_raised(monkeypatch):
    _patch_redis(monkeypatch, FakeRedis(broken=True))
    out = await incident_context.collect_redis()
    assert out["reachable"] is False
    assert out["keys"] == {}


# ── combined, and the flush it feeds ───────────────────────────────────────

async def test_collect_survives_both_being_down(monkeypatch):
    def boom(req):
        raise httpx.ConnectError("refused")
    _patch_mediamtx(monkeypatch, boom)
    _patch_redis(monkeypatch, FakeRedis(broken=True))
    out = await incident_context.collect()
    assert out["mediamtx"]["reachable"] is False
    assert out["redis"]["reachable"] is False


async def test_flush_with_snapshot_writes_both(monkeypatch, tmp_path):
    _patch_mediamtx(monkeypatch, lambda req: httpx.Response(200, json=PATHS_PAYLOAD))
    _patch_redis(monkeypatch, FakeRedis({"ww:pipeline:status": "running"}))

    buf = IncidentBuffer(IncidentBufferConfig(output_dir=tmp_path))
    record = logging.LogRecord("t", logging.DEBUG, __file__, 1, "a debug line", None, None)
    buf.emit(record)

    path = await incident_context.flush_with_snapshot(
        buf, "inc-1", extra={"reason": "camera loss"}
    )
    assert path is not None
    text = path.read_text()
    assert "a debug line" in text
    assert "cam-01" in text
    assert "ww:pipeline:status" in text
    assert "camera loss" in text

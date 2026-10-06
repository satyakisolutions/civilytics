"""Pipeline heartbeat: the file the Docker healthcheck reads."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import heartbeat  # noqa: E402


def test_write_then_fresh(tmp_path, monkeypatch):
    hb = tmp_path / "pipeline.heartbeat"
    monkeypatch.setattr(heartbeat, "HEARTBEAT_PATH", hb)
    heartbeat.write_heartbeat(None, active_sources=3, fps=14.7)
    data = json.loads(hb.read_text())
    assert data["active_sources"] == 3
    assert data["fps"] == 14.7
    assert data["state"] == "running"
    assert heartbeat.is_fresh(path=hb) is True


def test_stale_heartbeat_is_not_fresh(tmp_path):
    hb = tmp_path / "pipeline.heartbeat"
    hb.write_text(json.dumps({"ts": time.time() - 600, "state": "running"}))
    assert heartbeat.is_fresh(path=hb) is False


def test_missing_file_is_not_fresh(tmp_path):
    assert heartbeat.is_fresh(path=tmp_path / "nope") is False


def test_garbage_file_is_not_fresh(tmp_path):
    hb = tmp_path / "pipeline.heartbeat"
    hb.write_text("not json")
    assert heartbeat.is_fresh(path=hb) is False


def test_redis_failure_does_not_raise(tmp_path, monkeypatch):
    """A Redis outage must not stop the heartbeat file being written."""
    hb = tmp_path / "pipeline.heartbeat"
    monkeypatch.setattr(heartbeat, "HEARTBEAT_PATH", hb)

    class _Broken:
        def set(self, *_a, **_k):
            raise ConnectionError("redis down")

    heartbeat.write_heartbeat(_Broken(), active_sources=1, fps=1.0)
    assert heartbeat.is_fresh(path=hb) is True


def test_redis_keys_carry_ttl(tmp_path, monkeypatch):
    """TTL matters: without it a dead pipeline keeps looking alive."""
    hb = tmp_path / "pipeline.heartbeat"
    monkeypatch.setattr(heartbeat, "HEARTBEAT_PATH", hb)
    calls = []

    class _Rec:
        def set(self, key, value, ex=None):
            calls.append((key, ex))

    heartbeat.write_heartbeat(_Rec(), active_sources=2, fps=10.0)
    assert {k for k, _ in calls} == {
        "ww:pipeline:status", "ww:pipeline:fps", "ww:pipeline:active_sources",
    }
    assert all(ex is not None and ex > 0 for _, ex in calls)

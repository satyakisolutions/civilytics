"""Tests for StreamWatcher dynamic stream management.

StreamWatcher depends on SourceManager which requires GStreamer (gi).
We mock those dependencies to test the logic without DeepStream.
"""
import sys
import time
from unittest.mock import MagicMock, patch

import pytest


# Mock gi and Gst before importing stream_watcher
gi_mock = MagicMock()
gi_mock.require_version = MagicMock()
sys.modules["gi"] = gi_mock
sys.modules["gi.repository"] = MagicMock()

# Mock confluent_kafka and orjson for kafka_producer import chain
sys.modules.setdefault("confluent_kafka", MagicMock())
sys.modules.setdefault("orjson", MagicMock())
sys.modules.setdefault("pydantic_settings", MagicMock())

# Now we can import
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "app"))

from stream_watcher import StreamWatcher, StreamState, StreamEntry


def _make_watcher():
    source_manager = MagicMock()
    source_manager.active_sources = {}
    health = MagicMock()
    health.check_health.return_value = []
    watcher = StreamWatcher(
        source_manager=source_manager,
        health=health,
        poll_interval_s=1.0,
        probe_timeout_s=1.0,
    )
    return watcher, source_manager, health


def test_register_source_reachable():
    watcher, source_manager, _ = _make_watcher()
    with patch.object(watcher, "_probe_rtsp", return_value=True):
        entry = watcher.register_source("cam-01", "rtsp://mediamtx:8554/cam-01")
    assert entry.state == StreamState.ACTIVE
    source_manager.add_source.assert_called_once()


def test_register_source_unreachable():
    watcher, source_manager, _ = _make_watcher()
    with patch.object(watcher, "_probe_rtsp", return_value=False):
        entry = watcher.register_source("cam-01", "rtsp://mediamtx:8554/cam-01")
    assert entry.state == StreamState.DOWN
    source_manager.add_source.assert_not_called()


def test_poll_reconnects_down():
    watcher, source_manager, health = _make_watcher()
    with patch.object(watcher, "_probe_rtsp", return_value=False):
        watcher.register_source("cam-01", "rtsp://x:8554/cam-01")
    entry = watcher._streams["cam-01"]
    entry.last_attempt = time.monotonic() - 100
    with patch.object(watcher, "_probe_rtsp", return_value=True):
        states = watcher.poll()
    assert states["cam-01"] == StreamState.ACTIVE


def test_poll_marks_timed_out_as_down():
    watcher, source_manager, health = _make_watcher()
    with patch.object(watcher, "_probe_rtsp", return_value=True):
        watcher.register_source("cam-01", "rtsp://x:8554/cam-01")
    health.check_health.return_value = ["cam-01"]
    states = watcher.poll()
    assert states["cam-01"] == StreamState.DOWN


def test_max_retries_marks_failed():
    watcher, _, _ = _make_watcher()
    with patch.object(watcher, "_probe_rtsp", return_value=False):
        entry = watcher.register_source("cam-01", "rtsp://x:8554/cam-01")
    entry.retry_count = entry.max_retries
    entry.last_attempt = time.monotonic() - 1000
    watcher.poll()
    assert entry.state == StreamState.FAILED


def test_active_and_down_counts():
    watcher, _, _ = _make_watcher()
    with patch.object(watcher, "_probe_rtsp", side_effect=[True, False]):
        watcher.register_source("cam-01", "rtsp://x:8554/cam-01")
        watcher.register_source("cam-02", "rtsp://x:8554/cam-02")
    assert watcher.active_count == 1
    assert watcher.down_count == 1


def test_unregister_removes_active():
    watcher, source_manager, _ = _make_watcher()
    with patch.object(watcher, "_probe_rtsp", return_value=True):
        watcher.register_source("cam-01", "rtsp://x:8554/cam-01")
    watcher.unregister_source("cam-01")
    assert "cam-01" not in watcher._streams
    source_manager.remove_source.assert_called_with("cam-01")

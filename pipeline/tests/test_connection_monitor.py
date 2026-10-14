"""Camera recovery tests without requiring GPU/GStreamer libraries."""
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


def make_monitor():
    modules = {name: MagicMock() for name in (
        "orjson", "app.health", "app.kafka_producer",
        "app.output_manager", "app.source_manager",
    )}
    spec = importlib.util.spec_from_file_location(
        "camera_recovery_under_test",
        Path(__file__).parents[1] / "app" / "connection_monitor.py",
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {**modules, spec.name: module}):
        spec.loader.exec_module(module)
    source, output, health, kafka = (MagicMock() for _ in range(4))
    health._last_frame = {}
    health.check_health.return_value = []
    source.add_source.return_value = 1
    output.add_output_branch.return_value = True
    monitor = module.ConnectionMonitor(source, output, health, kafka)
    monitor._probe_rtsp_options = MagicMock(return_value=(True, 1.0))
    monitor.register_camera("camera", "rtsp://camera.lan/live")
    return monitor, module, source, output, health


def test_camera_error_retains_registration_and_reconnects():
    monitor, module, source, output, health = make_monitor()
    monitor.camera_failed("camera", "RTSP disconnected")
    conn = monitor._connections["camera"]
    assert conn.state == module.ConnectionState.DOWN
    assert conn.uri == "rtsp://camera.lan/live"
    assert conn.source_id is None
    source.remove_source.assert_called_once_with("camera")
    output.remove_output_branch.assert_called_once_with("camera")
    health.clear.assert_called_once_with("camera")
    conn.last_attempt = -1000
    monitor.poll()
    assert conn.state == module.ConnectionState.ACTIVE
    assert source.add_source.call_count == 2


def test_missing_first_frame_enters_recovery():
    monitor, module, *_ = make_monitor()
    conn = monitor._connections["camera"]
    conn.last_state_change -= 31
    monitor.poll()
    assert conn.state == module.ConnectionState.DOWN
    assert "first frame" in conn.error


def test_output_creation_failure_does_not_report_active():
    monitor, module, source, output, _ = make_monitor()
    monitor.camera_failed("camera", "disconnected")
    output.add_output_branch.return_value = False
    monitor._connections["camera"].last_attempt = -1000
    monitor.poll()
    assert monitor._connections["camera"].state == module.ConnectionState.DOWN
    assert monitor._connections["camera"].source_id is None


def test_deleted_camera_does_not_reconnect():
    monitor, _, source, _, _ = make_monitor()
    monitor.unregister_camera("camera")
    monitor.poll()
    assert "camera" not in monitor._connections
    assert source.add_source.call_count == 1

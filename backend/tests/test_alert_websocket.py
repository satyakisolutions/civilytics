"""Tests for alerts/websocket.py push_alert_ws."""
import pytest
from unittest.mock import patch, AsyncMock


@pytest.mark.asyncio
async def test_push_alert_ws():
    from app.alerts.websocket import push_alert_ws
    with patch("app.alerts.websocket.broadcast_alert", new_callable=AsyncMock) as mock_broadcast:
        await push_alert_ws(
            camera_id="cam-01", severity="critical", message="test alert",
            track_id=5, dwell_time_s=20.0, vlm_summary="dumping detected",
            plate_number="KL-01-AB-1234", snapshot_url="http://img.jpg",
        )
    mock_broadcast.assert_called_once()
    payload = mock_broadcast.call_args[0][0]
    assert payload["type"] == "alert"
    assert payload["camera_id"] == "cam-01"
    assert payload["severity"] == "critical"
    assert payload["track_id"] == 5
    assert payload["plate_number"] == "KL-01-AB-1234"


@pytest.mark.asyncio
async def test_push_alert_ws_minimal():
    from app.alerts.websocket import push_alert_ws
    with patch("app.alerts.websocket.broadcast_alert", new_callable=AsyncMock) as mock_broadcast:
        await push_alert_ws(camera_id="cam-02", severity="info", message="test")
    payload = mock_broadcast.call_args[0][0]
    assert payload["track_id"] is None
    assert payload["plate_number"] is None

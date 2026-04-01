"""Tests for AlertDispatcher."""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_handle_message_skips_info_non_dumping():
    from app.alerts.dispatcher import AlertDispatcher
    d = AlertDispatcher.__new__(AlertDispatcher)
    d._throttle = AsyncMock()
    d._telegram = AsyncMock()

    await d._handle_message({"camera_id": "cam-01", "severity": "info", "is_dumping": False})
    d._throttle.should_alert.assert_not_called()


@pytest.mark.asyncio
async def test_handle_message_dispatches_on_dumping():
    from app.alerts.dispatcher import AlertDispatcher
    d = AlertDispatcher.__new__(AlertDispatcher)
    d._throttle = AsyncMock()
    d._throttle.should_alert.return_value = True
    d._telegram = AsyncMock()
    d._telegram.send_alert.return_value = []

    with patch("app.alerts.dispatcher.push_alert_ws", new_callable=AsyncMock) as mock_ws:
        await d._handle_message({
            "camera_id": "cam-01", "severity": "critical", "is_dumping": True,
            "message": "Dumping detected",
        })

    d._telegram.send_alert.assert_called_once()
    mock_ws.assert_called_once()


@pytest.mark.asyncio
async def test_handle_message_throttled():
    from app.alerts.dispatcher import AlertDispatcher
    d = AlertDispatcher.__new__(AlertDispatcher)
    d._throttle = AsyncMock()
    d._throttle.should_alert.return_value = False
    d._telegram = AsyncMock()

    await d._handle_message({
        "camera_id": "cam-01", "severity": "critical", "is_dumping": True,
    })
    d._telegram.send_alert.assert_not_called()


@pytest.mark.asyncio
async def test_handle_message_records_before_fanout():
    from app.alerts.dispatcher import AlertDispatcher
    d = AlertDispatcher.__new__(AlertDispatcher)
    d._throttle = AsyncMock()
    d._throttle.should_alert.return_value = True
    d._telegram = AsyncMock()
    d._telegram.send_alert.return_value = []

    d._throttle.record_alert = AsyncMock()

    with patch("app.alerts.dispatcher.push_alert_ws", new_callable=AsyncMock):
        await d._handle_message({"camera_id": "cam-01", "severity": "high", "is_dumping": True})

    d._throttle.record_alert.assert_called_once()


@pytest.mark.asyncio
async def test_handle_message_info_but_dumping():
    """Info severity but is_dumping=True should still dispatch."""
    from app.alerts.dispatcher import AlertDispatcher
    d = AlertDispatcher.__new__(AlertDispatcher)
    d._throttle = AsyncMock()
    d._throttle.should_alert.return_value = True
    d._telegram = AsyncMock()
    d._telegram.send_alert.return_value = []

    with patch("app.alerts.dispatcher.push_alert_ws", new_callable=AsyncMock):
        await d._handle_message({"camera_id": "cam-01", "severity": "info", "is_dumping": True})

    d._telegram.send_alert.assert_called_once()

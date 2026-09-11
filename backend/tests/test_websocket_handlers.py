"""Tests for WebSocket broadcast functions."""
import pytest
from unittest.mock import AsyncMock
from app.ws import metadata as ws_meta, events as ws_events, alerts as ws_alerts


@pytest.fixture(autouse=True)
def clear_subscribers():
    """Clear all subscribers before each test."""
    ws_meta._subscribers.clear()
    ws_events._subscribers.clear()
    ws_alerts._subscribers.clear()
    yield
    ws_meta._subscribers.clear()
    ws_events._subscribers.clear()
    ws_alerts._subscribers.clear()


# ── Metadata broadcast ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_metadata_broadcast_to_camera():
    ws = AsyncMock()
    ws_meta._subscribers["cam-01"].add(ws)
    await ws_meta.broadcast_to_camera("cam-01", {"frame": 1})
    ws.send_json.assert_called_once_with({"frame": 1})

@pytest.mark.asyncio
async def test_metadata_broadcast_to_all():
    ws1 = AsyncMock()
    ws2 = AsyncMock()
    ws_meta._subscribers["cam-01"].add(ws1)
    ws_meta._subscribers["__all__"].add(ws2)
    await ws_meta.broadcast_to_all({"camera_id": "cam-01", "frame": 1})
    ws1.send_json.assert_called_once()
    ws2.send_json.assert_called_once()

@pytest.mark.asyncio
async def test_metadata_broadcast_removes_dead():
    ws = AsyncMock()
    ws.send_json.side_effect = Exception("closed")
    ws_meta._subscribers["cam-01"].add(ws)
    await ws_meta.broadcast_to_camera("cam-01", {"frame": 1})
    assert ws not in ws_meta._subscribers["cam-01"]

@pytest.mark.asyncio
async def test_metadata_broadcast_empty():
    await ws_meta.broadcast_to_camera("cam-99", {"frame": 1})  # No subscribers


# ── Events broadcast ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_events_broadcast():
    ws = AsyncMock()
    ws_events._subscribers.add(ws)
    await ws_events.broadcast_event({"state": "violation"})
    ws.send_json.assert_called_once_with({"state": "violation"})

@pytest.mark.asyncio
async def test_events_broadcast_removes_dead():
    ws = AsyncMock()
    ws.send_json.side_effect = Exception("disconnected")
    ws_events._subscribers.add(ws)
    await ws_events.broadcast_event({"state": "exited"})
    assert ws not in ws_events._subscribers


# ── Alerts broadcast ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_alerts_broadcast():
    ws = AsyncMock()
    ws_alerts._subscribers.add(ws)
    await ws_alerts.broadcast_alert({"severity": "critical"})
    ws.send_json.assert_called_once()

@pytest.mark.asyncio
async def test_alerts_broadcast_removes_dead():
    ws = AsyncMock()
    ws.send_json.side_effect = Exception("closed")
    ws_alerts._subscribers.add(ws)
    await ws_alerts.broadcast_alert({"severity": "info"})
    assert ws not in ws_alerts._subscribers

@pytest.mark.asyncio
async def test_alerts_broadcast_multiple():
    ws1 = AsyncMock()
    ws2 = AsyncMock()
    ws_alerts._subscribers.add(ws1)
    ws_alerts._subscribers.add(ws2)
    await ws_alerts.broadcast_alert({"test": True})
    ws1.send_json.assert_called_once()
    ws2.send_json.assert_called_once()

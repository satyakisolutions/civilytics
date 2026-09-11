"""Tests for TelegramAlert."""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_send_alert_no_token():
    from app.alerts.telegram import TelegramAlert
    t = TelegramAlert(bot_token="", chat_ids=[])
    results = await t.send_alert("cam-01", "critical", "test")
    assert results == []
    await t.close()


@pytest.mark.asyncio
async def test_send_alert_no_chat_ids():
    from app.alerts.telegram import TelegramAlert
    t = TelegramAlert(bot_token="token123", chat_ids=[])
    results = await t.send_alert("cam-01", "critical", "test")
    assert results == []
    await t.close()


@pytest.mark.asyncio
async def test_send_alert_text_only():
    from app.alerts.telegram import TelegramAlert
    t = TelegramAlert(bot_token="tok", chat_ids=["123"])
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"ok": True}

    with patch.object(t._client, "post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:
        results = await t.send_alert("cam-01", "critical", "Alert!")

    assert len(results) == 1
    assert results[0]["ok"] is True
    # Should call sendMessage, not sendPhoto
    call_url = mock_post.call_args[0][0]
    assert "sendMessage" in call_url
    await t.close()


@pytest.mark.asyncio
async def test_send_alert_with_photo():
    from app.alerts.telegram import TelegramAlert
    t = TelegramAlert(bot_token="tok", chat_ids=["123"])
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"ok": True}

    with patch.object(t._client, "post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:
        results = await t.send_alert("cam-01", "high", "Alert!", snapshot_url="http://img.jpg")

    call_url = mock_post.call_args[0][0]
    assert "sendPhoto" in call_url
    await t.close()


@pytest.mark.asyncio
async def test_send_alert_multiple_chat_ids():
    from app.alerts.telegram import TelegramAlert
    t = TelegramAlert(bot_token="tok", chat_ids=["111", "222"])
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"ok": True}

    with patch.object(t._client, "post", new_callable=AsyncMock, return_value=mock_resp):
        results = await t.send_alert("cam-01", "medium", "test")

    assert len(results) == 2
    await t.close()


@pytest.mark.asyncio
async def test_send_alert_partial_failure():
    from app.alerts.telegram import TelegramAlert
    t = TelegramAlert(bot_token="tok", chat_ids=["good", "bad"])

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"ok": True}

    with patch.object(t._client, "post", new_callable=AsyncMock, side_effect=[mock_resp, Exception("fail")]):
        results = await t.send_alert("cam-01", "critical", "test")

    assert len(results) == 2
    assert results[0]["ok"] is True
    assert results[1]["ok"] is False
    await t.close()


def test_format_message():
    from app.alerts.telegram import TelegramAlert
    t = TelegramAlert(bot_token="tok", chat_ids=[])
    msg = t._format_message(
        camera_id="cam-01", severity="critical", message="Dumping",
        dwell_time_s=25.3, vlm_summary="Person dumping waste", plate_number="KL-01-AB-1234",
        detected_at=1788351429.0,
    )
    assert "cam-01" in msg
    assert "CRITICAL" in msg
    assert "Dwell time" not in msg
    assert "IST" in msg
    assert ":" in msg
    assert "KL-01-AB-1234" in msg
    assert "Person dumping waste" in msg


def test_format_message_minimal():
    from app.alerts.telegram import TelegramAlert
    t = TelegramAlert(bot_token="tok", chat_ids=[])
    msg = t._format_message("cam-01", "info", "test", None, None, None)
    assert "cam-01" in msg
    assert "Dwell" not in msg
    assert "Plate" not in msg

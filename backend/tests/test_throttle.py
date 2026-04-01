import pytest
from unittest.mock import AsyncMock, patch
import time

from app.alerts.throttle import AlertThrottle


def _make_throttle(mock_client):
    throttle = AlertThrottle.__new__(AlertThrottle)
    throttle._redis = mock_client
    return throttle


@pytest.mark.asyncio
async def test_should_alert_no_prior():
    mock_client = AsyncMock()
    mock_client.get.return_value = None
    throttle = _make_throttle(mock_client)
    result = await throttle.should_alert("cam-01")
    assert result is True


@pytest.mark.asyncio
async def test_should_alert_within_cooldown():
    mock_client = AsyncMock()
    mock_client.get.return_value = str(int(time.time()) - 10)
    throttle = _make_throttle(mock_client)
    result = await throttle.should_alert("cam-01", cooldown_s=60)
    assert result is False


@pytest.mark.asyncio
async def test_should_alert_after_cooldown():
    mock_client = AsyncMock()
    # last alert was 90 seconds ago, cooldown is 60 — should allow
    mock_client.get.side_effect = [str(int(time.time()) - 90), None]
    throttle = _make_throttle(mock_client)
    result = await throttle.should_alert("cam-01", cooldown_s=60)
    assert result is True


@pytest.mark.asyncio
async def test_should_alert_hourly_limit_exceeded():
    mock_client = AsyncMock()
    # No cooldown block (last alert was long ago), but hourly count at limit
    mock_client.get.side_effect = [str(int(time.time()) - 120), b"10"]
    throttle = _make_throttle(mock_client)
    result = await throttle.should_alert("cam-01", cooldown_s=60, max_per_hour=10)
    assert result is False


@pytest.mark.asyncio
async def test_should_alert_hourly_limit_not_reached():
    mock_client = AsyncMock()
    # No cooldown block, hourly count below limit
    mock_client.get.side_effect = [str(int(time.time()) - 120), b"5"]
    throttle = _make_throttle(mock_client)
    result = await throttle.should_alert("cam-01", cooldown_s=60, max_per_hour=10)
    assert result is True


@pytest.mark.asyncio
async def test_record_alert():
    mock_client = AsyncMock()
    mock_pipe = AsyncMock()
    mock_client.pipeline.return_value = mock_pipe
    throttle = _make_throttle(mock_client)
    await throttle.record_alert("cam-01", cooldown_s=60)
    mock_client.set.assert_called_once()
    call_args = mock_client.set.call_args
    assert call_args[0][0] == "throttle:camera:cam-01"
    assert call_args[1]["ex"] == 60
    mock_pipe.incr.assert_called_once_with("throttle:hourly:cam-01")
    mock_pipe.expire.assert_called_once_with("throttle:hourly:cam-01", 3600)
    mock_pipe.execute.assert_called_once()


@pytest.mark.asyncio
async def test_close():
    mock_client = AsyncMock()
    throttle = _make_throttle(mock_client)
    await throttle.close()
    mock_client.aclose.assert_called_once()

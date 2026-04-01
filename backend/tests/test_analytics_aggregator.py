"""Tests for analytics aggregator."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone


@pytest.mark.asyncio
async def test_get_summary():
    from app.analytics.aggregator import get_summary
    mock_db = AsyncMock()
    mock_db.scalar.return_value = 42
    result = await get_summary(mock_db)
    assert isinstance(result, dict)
    assert result["total_events"] == 42
    assert mock_db.scalar.call_count == 6  # total, today, week, cameras_online, cameras_total, critical


@pytest.mark.asyncio
async def test_get_hourly_stats():
    from app.analytics.aggregator import get_hourly_stats
    mock_db = AsyncMock()
    mock_db.execute.return_value = MagicMock(
        all=MagicMock(return_value=[])
    )
    result = await get_hourly_stats(mock_db)
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_get_by_zone():
    from app.analytics.aggregator import get_by_zone
    mock_db = AsyncMock()
    mock_db.execute.return_value = MagicMock(
        all=MagicMock(return_value=[])
    )
    result = await get_by_zone(mock_db)
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_get_by_camera():
    from app.analytics.aggregator import get_by_camera
    mock_db = AsyncMock()
    mock_db.execute.return_value = MagicMock(
        all=MagicMock(return_value=[])
    )
    result = await get_by_camera(mock_db)
    assert isinstance(result, list)

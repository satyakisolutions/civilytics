# backend/app/analytics/aggregator.py
"""Analytics aggregation queries."""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ViolationEvent, Camera, TrackSession, AlertSeverity

logger = logging.getLogger(__name__)


async def get_summary(db: AsyncSession) -> dict:
    """Overall analytics summary."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = now - timedelta(days=7)

    total = await db.scalar(select(func.count(ViolationEvent.id)))
    today = await db.scalar(
        select(func.count(ViolationEvent.id)).where(ViolationEvent.timestamp >= today_start)
    )
    this_week = await db.scalar(
        select(func.count(ViolationEvent.id)).where(ViolationEvent.timestamp >= week_ago)
    )
    cameras_online = await db.scalar(
        select(func.count(Camera.id)).where(Camera.status == "online")
    )
    cameras_total = await db.scalar(select(func.count(Camera.id)))

    critical_today = await db.scalar(
        select(func.count(ViolationEvent.id)).where(
            ViolationEvent.timestamp >= today_start,
            ViolationEvent.severity == AlertSeverity.critical,
        )
    )

    return {
        "total_events": total or 0,
        "events_today": today or 0,
        "events_this_week": this_week or 0,
        "critical_today": critical_today or 0,
        "cameras_online": cameras_online or 0,
        "cameras_total": cameras_total or 0,
    }


async def get_hourly_stats(db: AsyncSession, hours: int = 24) -> list[dict]:
    """Hourly event counts for the last N hours."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    result = await db.execute(
        select(
            func.date_trunc("hour", ViolationEvent.timestamp).label("hour"),
            func.count(ViolationEvent.id).label("count"),
        )
        .where(ViolationEvent.timestamp >= since)
        .group_by("hour")
        .order_by("hour")
    )
    return [{"hour": row.hour.isoformat(), "count": row.count} for row in result]


async def get_by_zone(db: AsyncSession) -> list[dict]:
    """Event counts grouped by camera zone."""
    result = await db.execute(
        select(
            Camera.zone,
            func.count(ViolationEvent.id).label("event_count"),
        )
        .join(Camera, ViolationEvent.camera_id == Camera.id)
        .group_by(Camera.zone)
        .order_by(func.count(ViolationEvent.id).desc())
    )
    return [{"zone": row.zone, "event_count": row.event_count} for row in result]


async def get_by_camera(db: AsyncSession) -> list[dict]:
    """Event counts grouped by camera."""
    result = await db.execute(
        select(
            Camera.id,
            Camera.name,
            Camera.zone,
            func.count(ViolationEvent.id).label("event_count"),
        )
        .join(Camera, ViolationEvent.camera_id == Camera.id)
        .group_by(Camera.id, Camera.name, Camera.zone)
        .order_by(func.count(ViolationEvent.id).desc())
    )
    return [
        {"camera_id": str(row.id), "name": row.name, "zone": row.zone, "event_count": row.event_count}
        for row in result
    ]

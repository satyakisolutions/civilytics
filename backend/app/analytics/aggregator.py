# backend/app/analytics/aggregator.py
"""Analytics aggregation queries."""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ViolationEvent, Camera, TrackSession, AlertSeverity, AlertStatus, Zone

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

    resolved = await db.scalar(
        select(func.count(ViolationEvent.id)).where(ViolationEvent.status == AlertStatus.resolved)
    )
    pending = await db.scalar(
        select(func.count(ViolationEvent.id)).where(
            ViolationEvent.status.in_([AlertStatus.active, AlertStatus.acknowledged])
        )
    )

    # Repeat offenders: distinct person_id values with more than one
    # violation_events row. person_id is a free-text identifier (not a
    # real identity system -- see the privacy note in the govt briefing
    # doc), so this counts distinct tracked identifiers, not verified
    # individuals.
    repeat_offenders_subq = (
        select(ViolationEvent.person_id)
        .where(ViolationEvent.person_id.is_not(None))
        .group_by(ViolationEvent.person_id)
        .having(func.count(ViolationEvent.id) > 1)
    ).subquery()
    repeat_offenders = await db.scalar(select(func.count()).select_from(repeat_offenders_subq))

    # Average response time: time from event timestamp to acknowledgement,
    # for events that have actually been acknowledged. None (not 0) when
    # no events have been acknowledged yet, so the frontend can show
    # "no data" instead of a misleading zero.
    avg_response_seconds = await db.scalar(
        select(
            func.avg(
                func.extract("epoch", ViolationEvent.acknowledged_at - ViolationEvent.timestamp)
            )
        ).where(ViolationEvent.acknowledged_at.is_not(None))
    )
    avg_response_minutes = round(avg_response_seconds / 60, 1) if avg_response_seconds is not None else None

    return {
        "total_events": total or 0,
        "events_today": today or 0,
        "events_this_week": this_week or 0,
        "critical_today": critical_today or 0,
        "cameras_online": cameras_online or 0,
        "cameras_total": cameras_total or 0,
        "resolved_events": resolved or 0,
        "pending_events": pending or 0,
        "repeat_offenders": repeat_offenders or 0,
        "avg_response_time_minutes": avg_response_minutes,
    }


async def get_daily_trend(db: AsyncSession, days: int = 7) -> list[dict]:
    """Daily event counts for the last N days."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.execute(
        select(
            func.date_trunc("day", ViolationEvent.timestamp).label("day"),
            func.count(ViolationEvent.id).label("count"),
        )
        .where(ViolationEvent.timestamp >= since)
        .group_by("day")
        .order_by("day")
    )
    return [{"date": row.day.date().isoformat(), "count": row.count} for row in result]


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
    """Event counts grouped by zone.

    Grouped on Zone.id, not the name: grouping by name would merge two
    different zones that happen to share a label across tenants, and would
    re-split history whenever a zone is renamed.
    """
    result = await db.execute(
        select(
            Zone.name.label("zone"),
            func.count(ViolationEvent.id).label("event_count"),
        )
        .join(Camera, ViolationEvent.camera_id == Camera.id)
        .join(Zone, Camera.zone_id == Zone.id)
        .group_by(Zone.id, Zone.name)
        .order_by(func.count(ViolationEvent.id).desc())
    )
    return [{"zone": row.zone, "event_count": row.event_count} for row in result]


async def get_by_camera(db: AsyncSession) -> list[dict]:
    """Event counts grouped by camera."""
    result = await db.execute(
        select(
            Camera.id,
            Camera.name,
            Zone.name.label("zone"),
            func.count(ViolationEvent.id).label("event_count"),
        )
        .join(Camera, ViolationEvent.camera_id == Camera.id)
        .join(Zone, Camera.zone_id == Zone.id)
        .group_by(Camera.id, Camera.name, Zone.name)
        .order_by(func.count(ViolationEvent.id).desc())
    )
    return [
        {"camera_id": str(row.id), "name": row.name, "zone": row.zone, "event_count": row.event_count}
        for row in result
    ]

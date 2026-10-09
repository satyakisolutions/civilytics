# backend/app/analytics/aggregator.py
"""Analytics aggregation — reads from Redis (worker-populated) with DB fallback."""
import logging
from datetime import datetime, timedelta, timezone

import redis.asyncio as redis
from sqlalchemy import String, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.db.models import ViolationEvent, Camera, AlertSeverity, AlertStatus, Zone

logger = logging.getLogger(__name__)

_redis_pool: redis.Redis | None = None


def _get_redis() -> redis.Redis:
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = redis.from_url(settings.redis_url)
    return _redis_pool


async def _redis_int(key: str) -> int | None:
    """Get an int from Redis, None if missing."""
    try:
        val = await _get_redis().get(key)
        return int(val) if val is not None else None
    except Exception:
        return None


async def get_summary(db: AsyncSession) -> dict:
    """Overall analytics summary. Redis first, DB fallback."""
    r = _get_redis()

    # Try Redis
    try:
        pipe = r.pipeline()
        pipe.get("analytics:total")
        pipe.get("analytics:events_today")
        pipe.get("analytics:events_this_week")
        pipe.get("analytics:critical_today")
        pipe.get("analytics:resolved")
        pipe.get("analytics:pending")
        results = await pipe.execute()

        if results[0] is not None:
            # Redis has data — use it
            total = int(results[0] or 0)
            today = int(results[1] or 0)
            this_week = int(results[2] or 0)
            critical = int(results[3] or 0)
            resolved = int(results[4] or 0)
            pending = int(results[5] or 0)

            # Response time from Redis list
            raw_times = await r.lrange("analytics:response_times", 0, -1)
            if raw_times:
                avg_seconds = sum(float(t) for t in raw_times) / len(raw_times)
                avg_response_minutes = round(avg_seconds / 60, 1)
            else:
                avg_response_minutes = None

            # Repeat offenders: scan analytics:repeat:* keys with count > 1
            repeat_count = 0
            async for key in r.scan_iter("analytics:repeat:*"):
                val = await r.get(key)
                if val and int(val) > 1:
                    repeat_count += 1

            cameras_online = await db.scalar(
                select(func.count(Camera.id)).where(Camera.status == "online")
            )
            cameras_total = await db.scalar(select(func.count(Camera.id)))

            return {
                "total_events": total,
                "events_today": today,
                "events_this_week": this_week,
                "critical_today": critical,
                "cameras_online": cameras_online or 0,
                "cameras_total": cameras_total or 0,
                "resolved_events": resolved,
                "pending_events": pending,
                "repeat_offenders": repeat_count,
                "avg_response_time_minutes": avg_response_minutes,
            }
    except Exception:
        logger.debug("Redis unavailable, falling back to DB", exc_info=True)

    # DB fallback (original implementation)
    return await _get_summary_from_db(db)


async def _get_summary_from_db(db: AsyncSession) -> dict:
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

    repeat_offenders_subq = (
        select(ViolationEvent.person_id)
        .where(ViolationEvent.person_id.is_not(None))
        .group_by(ViolationEvent.person_id)
        .having(func.count(ViolationEvent.id) > 1)
    ).subquery()
    repeat_offenders = await db.scalar(select(func.count()).select_from(repeat_offenders_subq))

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
    """Daily event counts — Redis first, DB fallback."""
    try:
        r = _get_redis()
        now = datetime.now(timezone.utc)
        results = []
        for i in range(days - 1, -1, -1):
            day = now - timedelta(days=i)
            key = f"analytics:daily:{day.strftime('%Y%m%d')}"
            val = await r.get(key)
            if val is not None:
                results.append({"date": day.strftime("%Y-%m-%d"), "count": int(val)})
        if results:
            return results
    except Exception:
        pass

    # DB fallback
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
    """Hourly event counts — Redis first, DB fallback."""
    try:
        r = _get_redis()
        now = datetime.now(timezone.utc)
        results = []
        for i in range(hours - 1, -1, -1):
            hour = now - timedelta(hours=i)
            key = f"analytics:hourly:{hour.strftime('%Y%m%d%H')}"
            val = await r.get(key)
            if val is not None:
                results.append({"hour": hour.replace(minute=0, second=0, microsecond=0).isoformat(), "count": int(val)})
        if results:
            return results
    except Exception:
        pass

    # DB fallback
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
    """Zone event counts — Redis first, DB fallback."""
    try:
        r = _get_redis()
        zones = await r.zrevrangebyscore("analytics:zones", "+inf", "-inf", withscores=True)
        if zones:
            return [{"zone": z.decode() if isinstance(z, bytes) else z, "event_count": int(s)} for z, s in zones]
    except Exception:
        pass

    # DB fallback
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


async def get_daily_by_category(db: AsyncSession, days: int = 7) -> dict:
    """Daily event counts broken down by category."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.execute(
        select(
            func.date_trunc("day", ViolationEvent.timestamp).label("day"),
            func.coalesce(ViolationEvent.category, ViolationEvent.event_type.cast(String)).label("cat"),
            func.count(ViolationEvent.id).label("count"),
        )
        .where(ViolationEvent.timestamp >= since)
        .group_by("day", "cat")
        .order_by("day")
    )
    categories: set[str] = set()
    grid: dict[str, dict[str, int]] = {}
    for row in result:
        day = row.day.date().isoformat()
        cat = str(row.cat)
        categories.add(cat)
        grid.setdefault(day, {})[cat] = row.count

    cats = sorted(categories)
    data = []
    for i in range(days - 1, -1, -1):
        d = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
        row_data = {"date": d}
        for c in cats:
            row_data[c] = grid.get(d, {}).get(c, 0)
        data.append(row_data)
    return {"categories": cats, "data": data}


async def get_heatmap(db: AsyncSession, days: int = 7) -> dict:
    """Hour-of-day × category heatmap for the last N days."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.execute(
        select(
            func.extract("hour", ViolationEvent.timestamp).label("hour"),
            func.coalesce(ViolationEvent.category, ViolationEvent.event_type.cast(String)).label("cat"),
            func.count(ViolationEvent.id).label("count"),
        )
        .where(ViolationEvent.timestamp >= since)
        .group_by("hour", "cat")
    )
    categories: set[str] = set()
    grid: dict[int, dict[str, int]] = {}
    for row in result:
        h = int(row.hour)
        cat = str(row.cat)
        categories.add(cat)
        grid.setdefault(h, {})[cat] = row.count

    cats = sorted(categories)
    data = []
    for h in range(24):
        row_data = {"hour": h}
        for c in cats:
            row_data[c] = grid.get(h, {}).get(c, 0)
        data.append(row_data)
    return {"categories": cats, "data": data}


async def get_by_camera(db: AsyncSession) -> list[dict]:
    """Event counts by camera — always DB (not high-frequency enough to cache)."""
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

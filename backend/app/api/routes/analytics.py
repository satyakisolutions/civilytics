# backend/app/api/routes/analytics.py
import logging
from datetime import datetime
from pydantic import BaseModel
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db
from app.auth.middleware import require_viewer, require_admin
from app.analytics.aggregator import get_summary, get_hourly_stats, get_daily_trend, get_by_zone, get_by_camera, get_heatmap, get_daily_by_category
from app.config.settings import settings as app_settings

router = APIRouter(prefix="/analytics", tags=["analytics"])


class AnalyticsUpsertRequest(BaseModel):
    event_id: str
    workspace_id: str | None = None
    zone_id: str | None = None
    camera_id: str | None = None
    detection_type: str | None = None
    occurred_at: str | None = None
    confidence: float | None = None
    priority: str | None = None
    lpr_result: str | None = None
    cluster_result: str | None = None


@router.post("/upsert")
async def upsert_analytics(
    body: AnalyticsUpsertRequest,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    import redis.asyncio as aioredis

    version = 1
    now_key = datetime.utcnow().strftime("%Y-%m-%d")
    try:
        r = aioredis.from_url(app_settings.redis_url)
        pipe = r.pipeline()
        pipe.hincrby(f"analytics:daily:{now_key}", "total", 1)
        if body.detection_type:
            pipe.hincrby(f"analytics:daily:{now_key}", body.detection_type, 1)
        if body.zone_id:
            pipe.hincrby(f"analytics:by_zone:{now_key}", body.zone_id, 1)
        if body.camera_id:
            pipe.hincrby(f"analytics:by_camera:{now_key}", body.camera_id, 1)
        pipe.incr("analytics:version")
        results = await pipe.execute()
        version = results[-1]
        await r.aclose()
    except Exception as e:
        logging.getLogger(__name__).warning("Redis analytics update failed: %s", e)

    return {"version": version}

@router.get("/summary")
async def analytics_summary(db: AsyncSession = Depends(get_db), _user: dict = Depends(require_viewer)):
    return await get_summary(db)

@router.get("/hourly")
async def analytics_hourly(hours: int = Query(default=24, le=168), db: AsyncSession = Depends(get_db), _user: dict = Depends(require_viewer)):
    return await get_hourly_stats(db, hours)

@router.get("/daily")
async def analytics_daily(days: int = Query(default=7, le=90), db: AsyncSession = Depends(get_db), _user: dict = Depends(require_viewer)):
    return await get_daily_trend(db, days)

@router.get("/by-zone")
async def analytics_by_zone(db: AsyncSession = Depends(get_db), _user: dict = Depends(require_viewer)):
    return await get_by_zone(db)

@router.get("/daily-by-category")
async def analytics_daily_by_category(days: int = Query(default=7, le=90), db: AsyncSession = Depends(get_db), _user: dict = Depends(require_viewer)):
    return await get_daily_by_category(db, days)

@router.get("/heatmap")
async def analytics_heatmap(days: int = Query(default=7, le=90), db: AsyncSession = Depends(get_db), _user: dict = Depends(require_viewer)):
    return await get_heatmap(db, days)

@router.get("/by-camera")
async def analytics_by_camera(db: AsyncSession = Depends(get_db), _user: dict = Depends(require_viewer)):
    return await get_by_camera(db)

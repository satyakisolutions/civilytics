# backend/app/api/routes/analytics.py
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db
from app.analytics.aggregator import get_summary, get_hourly_stats, get_by_zone, get_by_camera

router = APIRouter(prefix="/analytics", tags=["analytics"])

@router.get("/summary")
async def analytics_summary(db: AsyncSession = Depends(get_db)):
    return await get_summary(db)

@router.get("/hourly")
async def analytics_hourly(hours: int = Query(default=24, le=168), db: AsyncSession = Depends(get_db)):
    return await get_hourly_stats(db, hours)

@router.get("/by-zone")
async def analytics_by_zone(db: AsyncSession = Depends(get_db)):
    return await get_by_zone(db)

@router.get("/by-camera")
async def analytics_by_camera(db: AsyncSession = Depends(get_db)):
    return await get_by_camera(db)

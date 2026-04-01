# backend/app/api/routes/lpr.py
import uuid
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db
from app.db.models import PlateRead

router = APIRouter(prefix="/lpr", tags=["lpr"])

@router.get("/plates", response_model=list[dict])
async def list_plates(
    camera_id: uuid.UUID | None = None,
    plate_number: str | None = None,
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
):
    query = select(PlateRead).order_by(desc(PlateRead.timestamp)).limit(limit)
    if camera_id:
        query = query.where(PlateRead.camera_id == camera_id)
    if plate_number:
        query = query.where(PlateRead.plate_number == plate_number)
    result = await db.execute(query)
    reads = result.scalars().all()
    return [
        {"id": str(r.id), "camera_id": str(r.camera_id),
         "plate_number": r.plate_number, "confidence": r.confidence,
         "timestamp": r.timestamp.isoformat()}
        for r in reads
    ]

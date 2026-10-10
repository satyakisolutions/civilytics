# backend/app/api/routes/lpr.py
import uuid
from pydantic import BaseModel
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db
from app.auth.middleware import require_viewer
from app.db.models import PlateRead, ViolationEvent, TrackSession

router = APIRouter(prefix="/lpr", tags=["lpr"])


class LPRAnalyzeRequest(BaseModel):
    event_id: str
    evidence_refs: list[str] = []
    workspace_id: str | None = None


@router.post("/analyze")
async def analyze_lpr(
    body: LPRAnalyzeRequest,
    db: AsyncSession = Depends(get_db),
):
    if not body.evidence_refs:
        return {"plates": []}

    plates = []
    try:
        event_uuid = uuid.UUID(body.event_id)
        evt_result = await db.execute(
            select(ViolationEvent).where(ViolationEvent.id == event_uuid)
        )
        event = evt_result.scalar_one_or_none()
        if event and event.track_id:
            ts_result = await db.execute(
                select(TrackSession).where(
                    TrackSession.camera_id == event.camera_id,
                    TrackSession.track_id == event.track_id,
                )
            )
            track = ts_result.scalar_one_or_none()
            if track:
                plate_result = await db.execute(
                    select(PlateRead).where(PlateRead.track_session_id == track.id)
                )
                for p in plate_result.scalars().all():
                    plates.append({
                        "plate_number": p.plate_number,
                        "plate_normalised": p.plate_normalised,
                        "confidence": p.confidence,
                        "timestamp": p.timestamp.isoformat(),
                    })
    except (ValueError, Exception):
        pass

    return {"plates": plates}

@router.get("/plates", response_model=list[dict])
async def list_plates(
    camera_id: uuid.UUID | None = None,
    plate_number: str | None = None,
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_viewer),
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

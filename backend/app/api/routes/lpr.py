# backend/app/api/routes/lpr.py
import uuid
from pydantic import BaseModel
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db
from app.auth.middleware import require_viewer, require_admin
from app.auth.tenancy import get_tenant, scope
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
    _user: dict = Depends(require_admin),
    tenant: str | None = Depends(get_tenant),
):
    if not body.evidence_refs:
        return {"plates": []}

    plates = []
    try:
        event_uuid = uuid.UUID(body.event_id)
        evt_result = await db.execute(
            scope(select(ViolationEvent).where(ViolationEvent.id == event_uuid), ViolationEvent, tenant)
        )
        event = evt_result.scalar_one_or_none()
        if event and event.track_id:
            ts_result = await db.execute(
                scope(select(TrackSession).where(
                    TrackSession.camera_id == event.camera_id,
                    TrackSession.track_id == event.track_id,
                ), TrackSession, tenant)
            )
            track = ts_result.scalar_one_or_none()
            if track:
                plate_result = await db.execute(
                    scope(select(PlateRead).where(PlateRead.track_session_id == track.id), PlateRead, tenant)
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
    tenant: str | None = Depends(get_tenant),
):
    query = scope(select(PlateRead).order_by(desc(PlateRead.timestamp)).limit(limit), PlateRead, tenant)
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

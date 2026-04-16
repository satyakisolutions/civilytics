import csv
import io
import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db
from app.auth.middleware import require_viewer, require_admin
from app.db.models import ViolationEvent, TrackSession, EventFSMState
from app.schemas.event import ViolationEventResponse, ViolationEventUpdate, TrackSessionResponse

router = APIRouter(prefix="/events", tags=["events"])


@router.get("", response_model=list[ViolationEventResponse])
async def list_events(
    camera_id: uuid.UUID | None = None,
    status: str | None = Query(default=None, description="Filter by status: active, acknowledged, resolved"),
    event_type: str | None = Query(default=None, description="Filter by event_type: dumping_detected, suspicious_activity, repeat_offender"),
    severity: str | None = Query(default=None, description="Filter by severity: critical, warning, info"),
    start_date: datetime | None = Query(default=None, description="Filter events after this timestamp (ISO 8601)"),
    end_date: datetime | None = Query(default=None, description="Filter events before this timestamp (ISO 8601)"),
    min_confidence: float | None = Query(default=None, ge=0.0, le=1.0),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_viewer),
):
    stmt = select(ViolationEvent).order_by(ViolationEvent.timestamp.desc())
    if camera_id is not None:
        stmt = stmt.where(ViolationEvent.camera_id == camera_id)
    if status is not None:
        stmt = stmt.where(ViolationEvent.status == status)
    if event_type is not None:
        stmt = stmt.where(ViolationEvent.event_type == event_type)
    if severity is not None:
        stmt = stmt.where(ViolationEvent.severity == severity)
    if start_date is not None:
        stmt = stmt.where(ViolationEvent.timestamp >= start_date)
    if end_date is not None:
        stmt = stmt.where(ViolationEvent.timestamp <= end_date)
    if min_confidence is not None:
        stmt = stmt.where(ViolationEvent.confidence >= min_confidence)
    stmt = stmt.offset(offset).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/export/csv")
async def export_events_csv(
    camera_id: uuid.UUID | None = None,
    status: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    limit: int = Query(default=1000, le=5000),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_viewer),
):
    """Export events as CSV for download."""
    stmt = select(ViolationEvent).order_by(ViolationEvent.timestamp.desc()).limit(limit)
    if camera_id is not None:
        stmt = stmt.where(ViolationEvent.camera_id == camera_id)
    if status is not None:
        stmt = stmt.where(ViolationEvent.status == status)
    if start_date is not None:
        stmt = stmt.where(ViolationEvent.timestamp >= start_date)
    if end_date is not None:
        stmt = stmt.where(ViolationEvent.timestamp <= end_date)
    result = await db.execute(stmt)
    events = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "camera_id", "timestamp", "event_type", "severity", "confidence", "dwell_time_s", "status", "notes"])
    for evt in events:
        writer.writerow([
            str(evt.id), str(evt.camera_id), evt.timestamp.isoformat() if evt.timestamp else "",
            evt.event_type, evt.severity, evt.confidence, evt.dwell_time_s, evt.status, evt.notes or "",
        ])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=wastewatch_events_{datetime.utcnow().strftime('%Y%m%d')}.csv"},
    )


@router.get("/tracks/active", response_model=list[TrackSessionResponse])
async def list_active_tracks(db: AsyncSession = Depends(get_db), _user: dict = Depends(require_viewer)):
    result = await db.execute(
        select(TrackSession).where(TrackSession.exited_at.is_(None)).order_by(TrackSession.entered_at.desc())
    )
    return result.scalars().all()


@router.get("/{event_id}", response_model=ViolationEventResponse)
async def get_event(event_id: uuid.UUID, db: AsyncSession = Depends(get_db), _user: dict = Depends(require_viewer)):
    result = await db.execute(select(ViolationEvent).where(ViolationEvent.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@router.patch("/{event_id}", response_model=ViolationEventResponse)
async def update_event(
    event_id: uuid.UUID,
    body: ViolationEventUpdate,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    result = await db.execute(select(ViolationEvent).where(ViolationEvent.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(event, field, value)
    await db.commit()
    await db.refresh(event)
    return event

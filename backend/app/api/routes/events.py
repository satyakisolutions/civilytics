import csv
import logging
import io
import uuid
from datetime import datetime
from pydantic import BaseModel as PydanticBaseModel
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db
from app.auth.middleware import require_viewer, require_admin
from app.auth.tenancy import get_tenant, scope
from app.db.models import ViolationEvent, TrackSession
from app.schemas.event import ViolationEventResponse, ViolationEventUpdate, TrackSessionResponse

router = APIRouter(prefix="/events", tags=["events"])


class EventPublishRequest(PydanticBaseModel):
    event_id: str
    workflow_id: str | None = None
    workspace_id: str | None = None
    zone_id: str | None = None
    camera_id: str | None = None
    detection_type: str | None = None
    confidence: float | None = None
    priority: str | None = None
    evidence_refs: list[str] = []
    lpr_result: list | None = None
    cluster_result: list | None = None
    analytics_status: str | None = None
    published_at: str | None = None


@router.post("/publish")
async def publish_event(
    body: EventPublishRequest,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
    tenant: str | None = Depends(get_tenant),
):
    try:
        event_uuid = uuid.UUID(body.event_id)
        result = await db.execute(
            scope(select(ViolationEvent).where(ViolationEvent.id == event_uuid), ViolationEvent, tenant)
        )
        event = result.scalar_one_or_none()
        if event:
            if body.lpr_result:
                event.relation_result = {
                    **(event.relation_result or {}),
                    "lpr_plates": body.lpr_result,
                }
            if body.cluster_result:
                event.relation_result = {
                    **(event.relation_result or {}),
                    "cluster_matches": body.cluster_result,
                }
            if body.evidence_refs:
                event.video_clip_url = event.video_clip_url or next(
                    (r for r in body.evidence_refs if r.endswith(".mp4")), None
                )
                event.snapshot_url = event.snapshot_url or next(
                    (r for r in body.evidence_refs if r.endswith((".jpg", ".png"))), None
                )
            await db.commit()
            return {"id": str(event.id)}
    except (ValueError, Exception) as e:
        logging.getLogger(__name__).warning("Event publish update failed for %s: %s", body.event_id, e)

    return {"id": body.event_id}


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
    tenant: str | None = Depends(get_tenant),
):
    stmt = scope(select(ViolationEvent).order_by(ViolationEvent.timestamp.desc()), ViolationEvent, tenant)
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
    tenant: str | None = Depends(get_tenant),
):
    """Export events as CSV for download."""
    stmt = scope(select(ViolationEvent).order_by(ViolationEvent.timestamp.desc()).limit(limit), ViolationEvent, tenant)
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
async def list_active_tracks(
    limit: int = Query(default=1000, le=5000),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_viewer),
    tenant: str | None = Depends(get_tenant),
):
    result = await db.execute(
        scope(
            select(TrackSession)
            .where(TrackSession.exited_at.is_(None))
            .order_by(TrackSession.entered_at.desc())
            .limit(limit),
            TrackSession,
            tenant,
        )
    )
    return result.scalars().all()


@router.get("/{event_id}", response_model=ViolationEventResponse)
async def get_event(event_id: uuid.UUID, db: AsyncSession = Depends(get_db), _user: dict = Depends(require_viewer), tenant: str | None = Depends(get_tenant)):
    result = await db.execute(scope(select(ViolationEvent).where(ViolationEvent.id == event_id), ViolationEvent, tenant))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@router.patch("/{event_id}", response_model=ViolationEventResponse)
async def update_event(
    event_id: uuid.UUID,
    body: ViolationEventUpdate,
    db: AsyncSession = Depends(get_db),
    # Recording a review verdict is operator work -- it is what the
    # "detection to first operator review" metric measures. Gating it on
    # `admin` meant no operator could mark anything reviewed.
    _user: dict = Depends(require_viewer),
    tenant: str | None = Depends(get_tenant),
):
    result = await db.execute(scope(select(ViolationEvent).where(ViolationEvent.id == event_id), ViolationEvent, tenant))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(event, field, value)
    await db.commit()
    await db.refresh(event)
    return event

import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db
from app.db.models import ViolationEvent, TrackSession, EventFSMState
from app.schemas.event import ViolationEventResponse, ViolationEventUpdate, TrackSessionResponse

router = APIRouter(prefix="/events", tags=["events"])


@router.get("", response_model=list[ViolationEventResponse])
async def list_events(
    camera_id: uuid.UUID | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ViolationEvent).order_by(ViolationEvent.timestamp.desc()).limit(limit)
    if camera_id is not None:
        stmt = stmt.where(ViolationEvent.camera_id == camera_id)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/tracks/active", response_model=list[TrackSessionResponse])
async def list_active_tracks(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(TrackSession).where(TrackSession.exited_at.is_(None)).order_by(TrackSession.entered_at.desc())
    )
    return result.scalars().all()


@router.get("/{event_id}", response_model=ViolationEventResponse)
async def get_event(event_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
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

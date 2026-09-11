import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.pipeline_notifier import PipelineNotifier
from app.api.deps import get_db
from app.auth.middleware import require_viewer, require_admin
from app.config.settings import settings
from app.db.models import Camera
from app.schemas.camera import CameraCreate, CameraResponse, CameraUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cameras", tags=["cameras"])

# Module-level singleton — reused across all requests.
_notifier = PipelineNotifier(settings.redis_url)

_ALLOWED_UPDATE_FIELDS = {"name", "zone", "status", "stream_url", "is_pinned", "location", "gps_lat", "gps_lng", "roi_polygons", "recording_config"}


@router.get("", response_model=list[CameraResponse])
async def list_cameras(db: AsyncSession = Depends(get_db), _user: dict = Depends(require_viewer)):
    result = await db.execute(select(Camera).order_by(Camera.is_pinned.desc(), Camera.name))
    return result.scalars().all()

@router.get("/{camera_id}", response_model=CameraResponse)
async def get_camera(camera_id: uuid.UUID, db: AsyncSession = Depends(get_db), _user: dict = Depends(require_viewer)):
    result = await db.execute(select(Camera).where(Camera.id == camera_id))
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    return camera

@router.post("", response_model=CameraResponse, status_code=201)
async def create_camera(body: CameraCreate, db: AsyncSession = Depends(get_db), _user: dict = Depends(require_admin)):
    camera = Camera(**body.model_dump())
    db.add(camera)
    await db.commit()
    await db.refresh(camera)

    # Notify pipeline to add the new RTSP source
    if camera.stream_url:
        _notifier._publish({
            "action": "add_camera",
            "camera_id": str(camera.id),
            "data": {
                "camera_name": camera.name,
                "uri": camera.stream_url,
                "zone": camera.zone,
            },
        })

    return camera

@router.patch("/{camera_id}", response_model=CameraResponse)
async def update_camera(camera_id: uuid.UUID, body: CameraUpdate, db: AsyncSession = Depends(get_db), _user: dict = Depends(require_admin)):
    result = await db.execute(select(Camera).where(Camera.id == camera_id))
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        if field not in _ALLOWED_UPDATE_FIELDS:
            continue
        setattr(camera, field, value)
    await db.commit()
    await db.refresh(camera)

    # Notify pipeline if the stream_url changed (or zone changed for same stream)
    if "stream_url" in updates or "zone" in updates:
        _notifier._publish({
            "action": "update_camera",
            "camera_id": str(camera.id),
            "data": {
                "uri": camera.stream_url,
                "zone": camera.zone,
            },
        })

    return camera

@router.delete("/{camera_id}", status_code=204)
async def delete_camera(camera_id: uuid.UUID, db: AsyncSession = Depends(get_db), _user: dict = Depends(require_admin)):
    result = await db.execute(select(Camera).where(Camera.id == camera_id))
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    # Notify pipeline before removing from DB
    _notifier._publish({
        "action": "remove_camera",
        "camera_id": str(camera.id),
    })

    await db.delete(camera)
    await db.commit()

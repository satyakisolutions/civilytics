import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.pipeline_notifier import PipelineNotifier
from app.api.deps import get_db
from app.auth.middleware import require_viewer, require_admin
from app.config.settings import settings
from app.db.models import Camera, Zone
from app.schemas.camera import CameraCreate, CameraResponse, CameraUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cameras", tags=["cameras"])

# Module-level singleton — reused across all requests.
_notifier = PipelineNotifier(settings.redis_url)

_ALLOWED_UPDATE_FIELDS = {"name", "zone", "status", "stream_url", "is_pinned", "location", "gps_lat", "gps_lng", "roi_polygons", "recording_config"}

# Bootstrap customer, created by migration 009. Every camera belongs to a
# tenant now, but tenant scoping on reads (T1) is NOT wired yet -- the
# caller's own tenant is not consulted here, so this remains a single-tenant
# deployment in practice until that work lands.
_DEFAULT_TENANT = uuid.UUID("00000000-0000-0000-0000-000000000001")


async def _resolve_zone(db: AsyncSession, name: str) -> Zone:
    """Zone by name within the tenant, created on first use.

    The API still speaks zone names, so a new name here creates the zone
    rather than rejecting the camera. It is created with is_speakable=False:
    a zone must not become broadcastable as a side effect of adding a
    camera, because that decision is what the flag exists to make explicit.
    """
    name = (name or "").strip() or "Unassigned"
    result = await db.execute(
        select(Zone).where(Zone.tenant_id == _DEFAULT_TENANT, Zone.name == name)
    )
    zone = result.scalar_one_or_none()
    if zone is None:
        zone = Zone(tenant_id=_DEFAULT_TENANT, name=name)
        db.add(zone)
        await db.flush()
        logger.info("Created zone %r (is_speakable=False until reviewed)", name)
    return zone


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
    payload = body.model_dump()
    zone = await _resolve_zone(db, payload.pop("zone", ""))
    camera = Camera(**payload, tenant_id=_DEFAULT_TENANT, zone_id=zone.id)
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
        if field == "zone":
            # `zone` is a read-only property over the FK now; setattr would
            # raise. Resolve the name to a zone row instead.
            camera.zone_id = (await _resolve_zone(db, value)).id
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

import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db
from app.db.models import Camera
from app.schemas.roi import ROIPolygonCreate, CameraROIResponse, ROIPolygonResponse

router = APIRouter(prefix="/cameras", tags=["roi"])


@router.get("/{camera_id}/roi", response_model=CameraROIResponse)
async def get_roi(camera_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Camera).where(Camera.id == camera_id))
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    raw = camera.roi_polygons or {}
    polygons = [
        ROIPolygonResponse(**p)
        for p in raw.get("polygons", [])
    ]
    return CameraROIResponse(camera_id=str(camera_id), polygons=polygons)


@router.put("/{camera_id}/roi", response_model=CameraROIResponse)
async def replace_roi(
    camera_id: uuid.UUID,
    polygons: list[ROIPolygonCreate],
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Camera).where(Camera.id == camera_id))
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    camera.roi_polygons = {"polygons": [p.model_dump() for p in polygons]}
    await db.commit()
    await db.refresh(camera)
    response_polygons = [ROIPolygonResponse(**p) for p in camera.roi_polygons.get("polygons", [])]
    return CameraROIResponse(camera_id=str(camera_id), polygons=response_polygons)


@router.delete("/{camera_id}/roi", status_code=204)
async def clear_roi(camera_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Camera).where(Camera.id == camera_id))
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    camera.roi_polygons = None
    await db.commit()

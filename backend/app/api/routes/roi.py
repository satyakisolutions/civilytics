import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db
from app.db.models import Camera
from app.schemas.roi import ROIPolygonCreate, CameraROIResponse, ROIPolygonResponse, ROIPoint, compute_bounding_rect

router = APIRouter(prefix="/cameras", tags=["roi"])


def _build_roi_response(camera_id: str, raw_polygons: list[dict]) -> CameraROIResponse:
    """Build ROI response with auto-derived bounding rects."""
    polygons = []
    all_points: list[ROIPoint] = []
    for p in raw_polygons:
        points = [ROIPoint(**pt) for pt in p.get("points", [])]
        rect = compute_bounding_rect(points) if points else None
        polygons.append(ROIPolygonResponse(name=p["name"], points=points, bounding_rect=rect))
        all_points.extend(points)

    merged_rect = compute_bounding_rect(all_points) if all_points else None
    return CameraROIResponse(
        camera_id=camera_id,
        polygons=polygons,
        merged_crop_rect=merged_rect,
    )


@router.get("/{camera_id}/roi", response_model=CameraROIResponse)
async def get_roi(camera_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Camera).where(Camera.id == camera_id))
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    raw = camera.roi_polygons or {}
    return _build_roi_response(str(camera_id), raw.get("polygons", []))


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
    return _build_roi_response(str(camera_id), camera.roi_polygons.get("polygons", []))


@router.delete("/{camera_id}/roi", status_code=204)
async def clear_roi(camera_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Camera).where(Camera.id == camera_id))
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    camera.roi_polygons = None
    await db.commit()

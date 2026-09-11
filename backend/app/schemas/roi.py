from pydantic import BaseModel, computed_field


class ROIPoint(BaseModel):
    x: float
    y: float


class BoundingRectResponse(BaseModel):
    """Axis-aligned bounding rectangle auto-derived from polygon points."""
    x: float
    y: float
    w: float
    h: float


class ROIPolygonCreate(BaseModel):
    name: str
    points: list[ROIPoint]


class ROIPolygonResponse(BaseModel):
    name: str
    points: list[ROIPoint]
    bounding_rect: BoundingRectResponse | None = None


class CameraROIResponse(BaseModel):
    camera_id: str
    polygons: list[ROIPolygonResponse]
    merged_crop_rect: BoundingRectResponse | None = None


def compute_bounding_rect(points: list[ROIPoint], padding: float = 0.05) -> BoundingRectResponse:
    """Compute the bounding rect from polygon points with padding."""
    xs = [p.x for p in points]
    ys = [p.y for p in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    w = max_x - min_x
    h = max_y - min_y
    pad_x = w * padding
    pad_y = h * padding
    return BoundingRectResponse(
        x=max(0.0, min_x - pad_x),
        y=max(0.0, min_y - pad_y),
        w=min(1.0 - max(0.0, min_x - pad_x), w + 2 * pad_x),
        h=min(1.0 - max(0.0, min_y - pad_y), h + 2 * pad_y),
    )

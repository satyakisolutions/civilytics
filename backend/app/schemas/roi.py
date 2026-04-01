from pydantic import BaseModel


class ROIPoint(BaseModel):
    x: float
    y: float


class ROIPolygonCreate(BaseModel):
    name: str
    points: list[ROIPoint]


class ROIPolygonResponse(BaseModel):
    name: str
    points: list[ROIPoint]


class CameraROIResponse(BaseModel):
    camera_id: str
    polygons: list[ROIPolygonResponse]

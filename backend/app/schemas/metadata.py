from pydantic import BaseModel


class Detection(BaseModel):
    class_id: int
    class_name: str  # person, car, truck, etc.
    confidence: float
    bbox: list[float]  # [x1, y1, x2, y2] normalized 0-1
    track_id: int | None = None


class FrameMetadata(BaseModel):
    camera_id: str
    frame_number: int
    timestamp: float  # epoch seconds
    detections: list[Detection]
    fps: float | None = None

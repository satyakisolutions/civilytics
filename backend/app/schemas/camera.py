import uuid
from datetime import datetime
from pydantic import BaseModel
from app.db.models import CameraStatus

class CameraCreate(BaseModel):
    name: str
    zone: str
    location: str | None = None
    gps_lat: float | None = None
    gps_lng: float | None = None
    stream_url: str | None = None
    source_type: str = "rtsp"

class CameraUpdate(BaseModel):
    name: str | None = None
    zone: str | None = None
    location: str | None = None
    gps_lat: float | None = None
    gps_lng: float | None = None
    stream_url: str | None = None
    source_type: str | None = None
    status: CameraStatus | None = None
    is_pinned: bool | None = None
    roi_polygons: dict | None = None
    recording_config: dict | None = None
    detection_config: dict | None = None

class CameraResponse(BaseModel):
    id: uuid.UUID
    name: str
    zone: str
    location: str | None
    gps_lat: float | None
    gps_lng: float | None
    status: CameraStatus
    stream_url: str | None
    source_type: str
    thumbnail_url: str | None
    roi_polygons: dict | None
    recording_config: dict | None
    detection_config: dict | None
    is_pinned: bool
    last_event_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

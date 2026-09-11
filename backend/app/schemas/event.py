import uuid
from datetime import datetime
from pydantic import BaseModel
from app.db.models import EventFSMState, AlertSeverity, AlertStatus, EventType


class TrackEventKafka(BaseModel):
    camera_id: str
    track_id: int
    state: str
    prev_state: str
    dwell_time_s: float
    class_name: str
    bbox: dict | None = None
    timestamp: float


class ViolationEventResponse(BaseModel):
    id: uuid.UUID
    camera_id: uuid.UUID
    timestamp: datetime
    event_type: EventType
    severity: AlertSeverity
    confidence: float
    track_id: int | None
    dwell_time_s: float | None
    status: AlertStatus
    person_id: str | None
    repeat_count: int
    snapshot_url: str | None
    video_clip_url: str | None
    notes: str | None
    created_at: datetime
    model_config = {"from_attributes": True}


class ViolationEventUpdate(BaseModel):
    status: AlertStatus | None = None
    notes: str | None = None


class TrackSessionResponse(BaseModel):
    id: uuid.UUID
    camera_id: uuid.UUID
    track_id: int
    fsm_state: str
    entered_at: datetime
    exited_at: datetime | None
    dwell_time_s: float | None
    created_at: datetime
    model_config = {"from_attributes": True}

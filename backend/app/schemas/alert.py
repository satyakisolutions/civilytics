import uuid
from datetime import datetime
from pydantic import BaseModel
from app.db.models import AlertSeverity, AlertStatus


class AlertResponse(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID | None = None
    camera_id: uuid.UUID | None = None
    severity: AlertSeverity
    title: str
    message: str | None = None
    zone: str | None = None
    channel: str | None = None
    tts_audio_url: str | None = None
    snapshot_url: str | None = None
    status: AlertStatus
    timestamp: datetime
    created_at: datetime
    model_config = {"from_attributes": True}


class AlertAcknowledge(BaseModel):
    status: AlertStatus = AlertStatus.acknowledged

import uuid
from datetime import datetime
from pydantic import BaseModel
from app.db.models import AlertSeverity, AlertStatus


class AlertResponse(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID | None
    camera_id: uuid.UUID | None
    severity: AlertSeverity
    title: str
    message: str | None
    zone: str | None
    channel: str | None
    tts_audio_url: str | None
    snapshot_url: str | None
    status: AlertStatus
    timestamp: datetime
    created_at: datetime
    model_config = {"from_attributes": True}


class AlertAcknowledge(BaseModel):
    status: AlertStatus = AlertStatus.acknowledged

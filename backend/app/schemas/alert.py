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
    incident_id: uuid.UUID | None = None
    # Face subject badge (docs/superpowers/specs/2026-09-29-face-subject-id-design.md D6).
    face_status: str | None = None  # None = pending/none, "matched", "no_face", "deleted"
    subject_id: uuid.UUID | None = None
    subject_code: str | None = None
    subject_incident_count: int | None = None
    subject_review_pending: bool = False
    model_config = {"from_attributes": True}


class AlertAcknowledge(BaseModel):
    status: AlertStatus = AlertStatus.acknowledged

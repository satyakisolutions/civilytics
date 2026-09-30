import uuid
from datetime import datetime

from pydantic import BaseModel


class SubjectSummary(BaseModel):
    id: uuid.UUID
    code: str
    incident_count: int
    first_seen_at: datetime
    last_seen_at: datetime
    cover_face_id: uuid.UUID | None
    camera_ids: list[str]


class SubjectFaceOut(BaseModel):
    id: uuid.UUID
    incident_id: uuid.UUID
    quality: float
    created_at: datetime


class SubjectIncidentOut(BaseModel):
    incident_id: uuid.UUID
    camera_id: str
    detected_at: datetime
    match_type: str
    similarity: float | None
    snapshot_url: str | None
    zone: str | None
    title: str | None


class SubjectDetail(SubjectSummary):
    status: str
    faces: list[SubjectFaceOut]
    incidents: list[SubjectIncidentOut]


class ReviewSide(BaseModel):
    subject_id: uuid.UUID
    code: str
    face_ids: list[uuid.UUID]


class ReviewOut(BaseModel):
    id: uuid.UUID
    incident_id: uuid.UUID
    similarity: float
    status: str
    created_at: datetime
    new_subject: ReviewSide
    candidate: ReviewSide


class ActionResult(BaseModel):
    ok: bool = True
    subject_id: uuid.UUID | None = None

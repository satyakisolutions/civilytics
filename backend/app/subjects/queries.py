"""Read-side queries for subject data (alert badges, subject pages, reviews)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select

from app.db.models import FaceIncident, Subject, SubjectIncident, SubjectMatchReview, subject_code


@dataclass(frozen=True)
class AlertBadge:
    face_status: str | None
    subject_id: uuid.UUID | None
    subject_code: str | None
    subject_incident_count: int | None
    subject_review_pending: bool


async def alert_badges(session, incident_ids: list[uuid.UUID]) -> dict[uuid.UUID, AlertBadge]:
    """Badge data per incident. Incidents still being processed are absent."""
    if not incident_ids:
        return {}
    review_pending = (
        select(SubjectMatchReview.id)
        .where(SubjectMatchReview.new_subject_id == Subject.id, SubjectMatchReview.status == "pending")
        .exists()
    )
    rows = (await session.execute(
        select(
            FaceIncident.incident_id, FaceIncident.status,
            Subject.id, Subject.code_num, Subject.incident_count, review_pending,
        )
        .outerjoin(SubjectIncident, SubjectIncident.incident_id == FaceIncident.incident_id)
        .outerjoin(Subject, Subject.id == SubjectIncident.subject_id)
        .where(FaceIncident.incident_id.in_(incident_ids))
    )).all()
    return {
        incident_id: AlertBadge(
            face_status=status,
            subject_id=subject_id,
            subject_code=subject_code(code_num) if code_num is not None else None,
            subject_incident_count=count,
            subject_review_pending=bool(pending) if subject_id is not None else False,
        )
        for incident_id, status, subject_id, code_num, count, pending in rows
    }

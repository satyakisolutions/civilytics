"""Read-side queries for subject data (alert badges, subject pages, reviews)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select

from app.db.models import Alert, FaceIncident, Subject, SubjectFace, SubjectIncident, SubjectMatchReview, subject_code


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


def _summary(subject: Subject, camera_ids: list[str]) -> dict:
    return {
        "id": subject.id,
        "code": subject.code,
        "incident_count": subject.incident_count,
        "first_seen_at": subject.first_seen_at,
        "last_seen_at": subject.last_seen_at,
        "cover_face_id": subject.cover_face_id,
        "camera_ids": camera_ids,
    }


async def _camera_ids(session, subject_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    if not subject_ids:
        return {}
    rows = (await session.execute(
        select(SubjectIncident.subject_id, SubjectIncident.camera_id)
        .where(SubjectIncident.subject_id.in_(subject_ids)).distinct()
    )).all()
    out: dict[uuid.UUID, list[str]] = {}
    for subject_id, camera_id in rows:
        out.setdefault(subject_id, []).append(camera_id)
    return {k: sorted(v) for k, v in out.items()}


async def list_subjects(
    session, repeat_only: bool = False, sort: str = "incidents", limit: int = 50, offset: int = 0,
) -> list[dict]:
    stmt = select(Subject).where(Subject.status == "active")
    if repeat_only:
        stmt = stmt.where(Subject.incident_count >= 2)
    if sort == "incidents":
        stmt = stmt.order_by(Subject.incident_count.desc(), Subject.last_seen_at.desc(), Subject.code_num)
    else:
        stmt = stmt.order_by(Subject.last_seen_at.desc(), Subject.code_num)
    subjects = (await session.execute(stmt.limit(limit).offset(offset))).scalars().all()
    cameras = await _camera_ids(session, [s.id for s in subjects])
    return [_summary(s, cameras.get(s.id, [])) for s in subjects]


async def subject_detail(session, subject_id: uuid.UUID) -> dict | None:
    subject = await session.get(Subject, subject_id)
    if subject is None or subject.status != "active":
        return None
    faces = (await session.execute(
        select(SubjectFace).where(SubjectFace.subject_id == subject_id).order_by(SubjectFace.quality.desc())
    )).scalars().all()
    # Several alerts can share one incident_id (a person track's violation
    # episode, spec D4): join only the earliest alert per incident.
    ranked = (
        select(
            Alert.incident_id, Alert.snapshot_url, Alert.zone, Alert.title,
            func.row_number().over(
                partition_by=Alert.incident_id, order_by=(Alert.timestamp, Alert.created_at, Alert.id),
            ).label("rn"),
        )
        .where(Alert.incident_id.in_(
            select(SubjectIncident.incident_id).where(SubjectIncident.subject_id == subject_id)
        ))
        .subquery()
    )
    first_alert = select(ranked).where(ranked.c.rn == 1).subquery()
    incidents = (await session.execute(
        select(SubjectIncident, first_alert.c.snapshot_url, first_alert.c.zone, first_alert.c.title)
        .outerjoin(first_alert, first_alert.c.incident_id == SubjectIncident.incident_id)
        .where(SubjectIncident.subject_id == subject_id)
        .order_by(SubjectIncident.detected_at.desc())
    )).all()
    cameras = await _camera_ids(session, [subject_id])
    return {
        **_summary(subject, cameras.get(subject_id, [])),
        "status": subject.status,
        "faces": [
            {"id": f.id, "incident_id": f.incident_id, "quality": f.quality, "created_at": f.created_at}
            for f in faces
        ],
        "incidents": [
            {
                "incident_id": inc.incident_id, "camera_id": inc.camera_id, "detected_at": inc.detected_at,
                "match_type": inc.match_type, "similarity": inc.similarity,
                "snapshot_url": snapshot_url, "zone": zone, "title": title,
            }
            for inc, snapshot_url, zone, title in incidents
        ],
    }


async def _review_side(session, subject_id: uuid.UUID) -> dict | None:
    subject = await session.get(Subject, subject_id)
    if subject is None:
        return None
    face_ids = list((await session.execute(
        select(SubjectFace.id).where(SubjectFace.subject_id == subject_id)
        .order_by(SubjectFace.quality.desc()).limit(3)
    )).scalars())
    return {"subject_id": subject.id, "code": subject.code, "face_ids": face_ids}


async def pending_review_count(session) -> int:
    return int((await session.execute(
        select(func.count()).select_from(SubjectMatchReview).where(SubjectMatchReview.status == "pending")
    )).scalar_one())


async def list_reviews(session, status: str = "pending") -> list[dict]:
    reviews = (await session.execute(
        select(SubjectMatchReview).where(SubjectMatchReview.status == status)
        .order_by(SubjectMatchReview.created_at.desc())
    )).scalars().all()
    out = []
    for review in reviews:
        new_side = await _review_side(session, review.new_subject_id)
        candidate = await _review_side(session, review.candidate_subject_id)
        if new_side is None or candidate is None:
            continue
        out.append({
            "id": review.id, "incident_id": review.incident_id, "similarity": review.similarity,
            "status": review.status, "created_at": review.created_at,
            "new_subject": new_side, "candidate": candidate,
        })
    return out

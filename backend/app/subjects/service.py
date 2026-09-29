"""Subject lifecycle: match violation faces to anonymous subjects.

Postgres is the source of truth (embeddings included); Qdrant is an index
updated after each commit. Faces whose index write failed keep
qdrant_synced=False and are re-indexed by resync_index(); if Qdrant search
itself fails, matching falls back to an exact search over Postgres.
See docs/superpowers/specs/2026-09-29-face-subject-id-design.md §6-8.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sqlalchemy import delete, func, select, text, update

from app.db.models import (
    FaceIncident,
    Subject,
    SubjectAccessLog,
    SubjectFace,
    SubjectIncident,
    SubjectMatchReview,
)
from app.subjects.config import SubjectSettings
from app.subjects.matcher import Hit, best_per_subject, decide, faces_over_cap
from app.subjects.types import AnalyzedFace, FaceRequest

logger = logging.getLogger(__name__)

SEARCH_LIMIT = 20


class NotFound(Exception):
    """The subject, review or incident does not exist."""


class Conflict(Exception):
    """The action no longer applies (review already decided, subjects changed, ...)."""


@dataclass(frozen=True)
class ProcessResult:
    status: str  # matched | no_face | duplicate
    subject_id: uuid.UUID | None = None
    subject_code: str | None = None
    match_type: str | None = None  # new | auto | review
    incident_count: int | None = None
    similarity: float | None = None


def embedding_to_bytes(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def embedding_from_bytes(data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype=np.float32)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SubjectService:
    def __init__(self, session_factory, vector_store, settings: SubjectSettings) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self._vs = vector_store
        self._faces_dir = Path(settings.faces_dir)

    # ── incident processing ────────────────────────────────────────

    async def process_request(self, req: FaceRequest, faces: list[AnalyzedFace]) -> ProcessResult:
        async with self.session_factory() as s:
            if await s.get(FaceIncident, req.incident_id) is not None:
                return ProcessResult("duplicate")
            if not faces:
                s.add(FaceIncident(
                    incident_id=req.incident_id, status="no_face",
                    camera_id=req.camera_id, detected_at=req.detected_at,
                ))
                await s.commit()
                self._remove_incoming(req.incident_id)
                return ProcessResult("no_face")

            per_subject = await self._score_subjects(s, [f.embedding for f in faces])
            decision = decide(per_subject, self.settings.match_auto, self.settings.match_review)
            if decision.kind == "auto":
                subject = await s.get(Subject, decision.subject_id)
            else:
                subject = await self._new_subject(s, req.detected_at)
            if decision.kind == "review":
                s.add(SubjectMatchReview(
                    incident_id=req.incident_id, new_subject_id=subject.id,
                    candidate_subject_id=decision.subject_id, similarity=decision.score,
                ))
            s.add(SubjectIncident(
                incident_id=req.incident_id, subject_id=subject.id, camera_id=req.camera_id,
                detected_at=req.detected_at,
                match_type="auto" if decision.kind == "auto" else "new",
                similarity=decision.score,
            ))
            new_rows: list[tuple[SubjectFace, AnalyzedFace]] = []
            for analyzed in faces:
                face_id = uuid.uuid4()
                row = SubjectFace(
                    id=face_id, subject_id=subject.id, incident_id=req.incident_id,
                    crop_path=str(self._face_path(subject.id, face_id)),
                    quality=analyzed.quality, embedding=embedding_to_bytes(analyzed.embedding),
                    qdrant_synced=False,
                )
                s.add(row)
                new_rows.append((row, analyzed))
            s.add(FaceIncident(
                incident_id=req.incident_id, status="matched",
                camera_id=req.camera_id, detected_at=req.detected_at,
            ))
            await s.flush()
            evicted = await self._enforce_face_cap(s, subject.id)
            await self._recompute(s, subject)
            await s.commit()

        evicted_ids = {row.id for row in evicted}
        kept = [(row, analyzed) for row, analyzed in new_rows if row.id not in evicted_ids]
        for row, analyzed in kept:
            path = Path(row.crop_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(analyzed.face_jpeg)
        await self._index_faces([row for row, _ in kept])
        await self._drop_faces(evicted)
        self._remove_incoming(req.incident_id)
        return ProcessResult(
            "matched", subject.id, subject.code, decision.kind, subject.incident_count, decision.score
        )

    # ── operator actions (spec §8) ─────────────────────────────────

    async def log_access(
        self, s, subject_id: uuid.UUID | None, action: str, actor: str,
        client_ip: str | None = None, detail: dict | None = None,
    ) -> None:
        s.add(SubjectAccessLog(
            subject_id=subject_id, action=action, actor=actor, client_ip=client_ip, detail=detail,
        ))

    async def merge_review(self, review_id: uuid.UUID, actor: str, client_ip: str | None = None) -> uuid.UUID:
        async with self.session_factory() as s:
            review = await s.get(SubjectMatchReview, review_id)
            if review is None:
                raise NotFound("review")
            if review.status != "pending":
                raise Conflict(f"review already {review.status}")
            src = await s.get(Subject, review.new_subject_id)
            dst = await s.get(Subject, review.candidate_subject_id)
            if src is None or dst is None or src.status != "active" or dst.status != "active":
                raise Conflict("subjects changed since this review was created")
            moved = list((await s.execute(
                select(SubjectFace).where(SubjectFace.subject_id == src.id)
            )).scalars())
            for row in moved:
                row.subject_id = dst.id
                row.qdrant_synced = False
            await s.execute(
                update(SubjectIncident).where(SubjectIncident.subject_id == src.id)
                .values(subject_id=dst.id, match_type="review_merged")
            )
            await self._repoint_reviews(s, old=src.id, new=dst.id, exclude=review.id)
            review.status, review.decided_by, review.decided_at = "merged", actor, utcnow()
            src.status, src.merged_into_id, src.incident_count, src.cover_face_id = "merged", dst.id, 0, None
            await s.flush()
            evicted = await self._enforce_face_cap(s, dst.id)
            await self._recompute(s, dst)
            await self.log_access(s, dst.id, "merge", actor, client_ip, {
                "review_id": str(review.id), "merged_subject_id": str(src.id),
            })
            await s.commit()
            target = dst.id
        evicted_ids = {r.id for r in evicted}
        await self._index_faces([r for r in moved if r.id not in evicted_ids])
        await self._drop_faces(evicted)
        return target

    async def _repoint_reviews(self, s, old: uuid.UUID, new: uuid.UUID, exclude: uuid.UUID) -> None:
        r = SubjectMatchReview
        await s.execute(update(r).where(r.id != exclude, r.new_subject_id == old).values(new_subject_id=new))
        await s.execute(update(r).where(r.id != exclude, r.candidate_subject_id == old).values(candidate_subject_id=new))
        await s.execute(
            update(r).where(r.status == "pending", r.new_subject_id == r.candidate_subject_id).values(status="obsolete")
        )

    async def reject_review(self, review_id: uuid.UUID, actor: str, client_ip: str | None = None) -> None:
        async with self.session_factory() as s:
            review = await s.get(SubjectMatchReview, review_id)
            if review is None:
                raise NotFound("review")
            if review.status != "pending":
                raise Conflict(f"review already {review.status}")
            review.status, review.decided_by, review.decided_at = "rejected", actor, utcnow()
            await self.log_access(s, review.new_subject_id, "reject", actor, client_ip, {
                "review_id": str(review.id), "candidate_subject_id": str(review.candidate_subject_id),
            })
            await s.commit()

    async def detach_incident(
        self, subject_id: uuid.UUID, incident_id: uuid.UUID, actor: str, client_ip: str | None = None,
    ) -> uuid.UUID:
        async with self.session_factory() as s:
            incident = await s.get(SubjectIncident, incident_id)
            if incident is None or incident.subject_id != subject_id:
                raise NotFound("incident")
            old = await s.get(Subject, subject_id)
            count = (await s.execute(
                select(func.count()).select_from(SubjectIncident)
                .where(SubjectIncident.subject_id == subject_id)
            )).scalar_one()
            if count <= 1:
                raise Conflict("this is the subject's only incident")
            new = await self._new_subject(s, incident.detected_at)
            incident.subject_id, incident.match_type = new.id, "detached"
            moved = list((await s.execute(
                select(SubjectFace).where(SubjectFace.subject_id == subject_id, SubjectFace.incident_id == incident_id)
            )).scalars())
            for row in moved:
                row.subject_id = new.id
                row.qdrant_synced = False
            await s.execute(
                update(SubjectMatchReview)
                .where(SubjectMatchReview.incident_id == incident_id, SubjectMatchReview.status == "pending")
                .values(status="obsolete")
            )
            await s.flush()
            await self._recompute(s, old)
            await self._recompute(s, new)
            await self.log_access(s, subject_id, "detach", actor, client_ip, {
                "incident_id": str(incident_id), "new_subject_id": str(new.id),
            })
            await s.commit()
            new_id = new.id
        await self._index_faces(moved)
        return new_id

    async def delete_subject(
        self, subject_id: uuid.UUID, actor: str, client_ip: str | None = None, reason: str = "operator",
    ) -> None:
        async with self.session_factory() as s:
            subject = await s.get(Subject, subject_id)
            if subject is None:
                raise NotFound("subject")
            code = subject.code
            faces = list((await s.execute(
                select(SubjectFace).where(SubjectFace.subject_id == subject_id)
            )).scalars())
            incident_ids = list((await s.execute(
                select(SubjectIncident.incident_id).where(SubjectIncident.subject_id == subject_id)
            )).scalars())
            await s.execute(delete(SubjectMatchReview).where(
                (SubjectMatchReview.new_subject_id == subject_id)
                | (SubjectMatchReview.candidate_subject_id == subject_id)
            ))
            await s.execute(delete(SubjectIncident).where(SubjectIncident.subject_id == subject_id))
            if incident_ids:
                await s.execute(
                    update(FaceIncident).where(FaceIncident.incident_id.in_(incident_ids)).values(status="deleted")
                )
            await s.execute(delete(SubjectFace).where(SubjectFace.subject_id == subject_id))
            await s.execute(update(Subject).where(Subject.merged_into_id == subject_id).values(merged_into_id=None))
            await s.delete(subject)
            await self.log_access(s, subject_id, "delete", actor, client_ip, {
                "code": code, "incidents": len(incident_ids), "reason": reason,
            })
            await s.commit()
        await self._drop_faces(faces)
        shutil.rmtree(self._faces_dir / "subjects" / str(subject_id), ignore_errors=True)

    # ── helpers (shared with operator actions / retention) ─────────

    async def _score_subjects(self, s, vectors: list[np.ndarray]) -> dict[uuid.UUID, float]:
        try:
            hits: list[Hit] = []
            for vector in vectors:
                hits.extend(await asyncio.to_thread(self._vs.search, vector, SEARCH_LIMIT))
        except Exception:
            logger.warning("Qdrant search failed; using exact search over Postgres", exc_info=True)
            hits = await self._exact_search(s, vectors)
        per_subject = best_per_subject(hits)
        if not per_subject:
            return {}
        active = set((await s.execute(
            select(Subject.id).where(Subject.id.in_(list(per_subject)), Subject.status == "active")
        )).scalars())
        return {sid: score for sid, score in per_subject.items() if sid in active}

    async def _exact_search(self, s, vectors: list[np.ndarray]) -> list[Hit]:
        rows = (await s.execute(
            select(SubjectFace.id, SubjectFace.subject_id, SubjectFace.embedding)
            .join(Subject, Subject.id == SubjectFace.subject_id)
            .where(Subject.status == "active")
        )).all()
        hits = []
        for face_id, subject_id, data in rows:
            stored = embedding_from_bytes(data)
            for vector in vectors:
                score = float(np.dot(stored, np.asarray(vector, dtype=np.float32)))
                hits.append(Hit(subject_id=subject_id, face_id=face_id, score=score))
        return hits

    async def _new_subject(self, s, seen_at: datetime) -> Subject:
        subject = Subject(
            code_num=await self._next_code_num(s), status="active", incident_count=0,
            first_seen_at=seen_at, last_seen_at=seen_at,
        )
        s.add(subject)
        await s.flush()
        return subject

    async def _next_code_num(self, s) -> int:
        if s.get_bind().dialect.name == "postgresql":
            return int((await s.execute(text("SELECT nextval('subject_code_seq')"))).scalar_one())
        current = (await s.execute(select(func.coalesce(func.max(Subject.code_num), 0)))).scalar_one()
        return int(current) + 1

    async def _recompute(self, s, subject: Subject) -> None:
        count, first, last = (await s.execute(
            select(func.count(), func.min(SubjectIncident.detected_at), func.max(SubjectIncident.detected_at))
            .where(SubjectIncident.subject_id == subject.id)
        )).one()
        subject.incident_count = int(count)
        if count:
            subject.first_seen_at, subject.last_seen_at = first, last
        subject.cover_face_id = (await s.execute(
            select(SubjectFace.id).where(SubjectFace.subject_id == subject.id)
            .order_by(SubjectFace.quality.desc(), SubjectFace.id).limit(1)
        )).scalar_one_or_none()

    async def _enforce_face_cap(self, s, subject_id: uuid.UUID) -> list[SubjectFace]:
        rows = (await s.execute(select(SubjectFace).where(SubjectFace.subject_id == subject_id))).scalars().all()
        over = set(faces_over_cap([(r.id, r.quality) for r in rows], self.settings.max_faces))
        evicted = [r for r in rows if r.id in over]
        for row in evicted:
            await s.delete(row)
        await s.flush()
        return evicted

    async def _index_faces(self, rows: list[SubjectFace]) -> int:
        synced = []
        for row in rows:
            try:
                await asyncio.to_thread(
                    self._vs.upsert, row.id, embedding_from_bytes(row.embedding), row.subject_id, row.incident_id
                )
                synced.append(row.id)
            except Exception:
                logger.warning("Qdrant upsert failed for face %s; will resync", row.id, exc_info=True)
        if synced:
            async with self.session_factory() as s:
                await s.execute(update(SubjectFace).where(SubjectFace.id.in_(synced)).values(qdrant_synced=True))
                await s.commit()
        return len(synced)

    async def _drop_faces(self, rows: list[SubjectFace]) -> None:
        if not rows:
            return
        try:
            await asyncio.to_thread(self._vs.delete, [r.id for r in rows])
        except Exception:
            logger.warning("Qdrant delete failed for %d faces", len(rows), exc_info=True)
        for row in rows:
            Path(row.crop_path).unlink(missing_ok=True)

    def _face_path(self, subject_id: uuid.UUID, face_id: uuid.UUID) -> Path:
        return self._faces_dir / "subjects" / str(subject_id) / f"{face_id}.jpg"

    def _remove_incoming(self, incident_id: uuid.UUID) -> None:
        shutil.rmtree(self._faces_dir / "incoming" / str(incident_id), ignore_errors=True)

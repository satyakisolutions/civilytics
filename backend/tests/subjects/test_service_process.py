from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, update

from app.db.models import FaceIncident, Subject, SubjectFace, SubjectMatchReview
from app.subjects.config import SubjectSettings
from app.subjects.service import SubjectService

from tests.subjects.factories import FailingStore, T0, face, request, unit, with_similarity


async def test_first_incident_creates_new_subject(service, session_factory, settings):
    req = request()
    result = await service.process_request(req, [face(unit(1))])
    assert (result.status, result.match_type, result.subject_code, result.incident_count) == (
        "matched", "new", "S-001", 1,
    )
    async with session_factory() as s:
        row = (await s.execute(select(SubjectFace))).scalar_one()
        assert row.qdrant_synced is True
        crop = Path(row.crop_path)
        assert crop.read_bytes().startswith(b"\xff\xd8")
        assert crop.is_relative_to(Path(settings.faces_dir) / "subjects" / str(result.subject_id))
        assert (await s.get(FaceIncident, req.incident_id)).status == "matched"


async def test_same_face_twice_is_auto_repeat(service):
    v = unit(1)
    first = await service.process_request(request(), [face(v)])
    second = await service.process_request(request(at=T0 + timedelta(days=3)), [face(with_similarity(v, 0.8))])
    assert second.match_type == "auto"
    assert second.subject_id == first.subject_id
    assert second.incident_count == 2
    assert second.similarity == pytest.approx(0.8, abs=1e-3)


async def test_uncertain_match_creates_new_subject_and_review(service, session_factory):
    v = unit(1)
    first = await service.process_request(request(), [face(v)])
    second = await service.process_request(request(), [face(with_similarity(v, 0.45))])
    assert (second.match_type, second.subject_code) == ("review", "S-002")
    async with session_factory() as s:
        review = (await s.execute(select(SubjectMatchReview))).scalar_one()
        assert review.status == "pending"
        assert review.new_subject_id == second.subject_id
        assert review.candidate_subject_id == first.subject_id
        assert review.similarity == pytest.approx(0.45, abs=1e-3)


async def test_different_face_is_new_without_review(service, session_factory):
    v = unit(1)
    await service.process_request(request(), [face(v)])
    second = await service.process_request(request(), [face(with_similarity(v, 0.2))])
    assert (second.match_type, second.subject_code) == ("new", "S-002")
    async with session_factory() as s:
        assert (await s.execute(select(func.count()).select_from(SubjectMatchReview))).scalar_one() == 0


async def test_no_faces_records_no_face_and_cleans_incoming(service, session_factory, settings):
    req = request()
    incoming = Path(settings.faces_dir) / "incoming" / str(req.incident_id)
    incoming.mkdir(parents=True)
    (incoming / "00.jpg").write_bytes(b"x")
    result = await service.process_request(req, [])
    assert result.status == "no_face"
    assert not incoming.exists()
    async with session_factory() as s:
        assert (await s.get(FaceIncident, req.incident_id)).status == "no_face"
        assert (await s.execute(select(func.count()).select_from(Subject))).scalar_one() == 0


async def test_duplicate_incident_is_ignored(service):
    req = request()
    await service.process_request(req, [face(unit(1))])
    again = await service.process_request(req, [face(unit(1))])
    assert again.status == "duplicate"
    third = await service.process_request(request(), [face(unit(1))])
    assert third.incident_count == 2


async def test_face_cap_keeps_best_quality(session_factory, vector_store, tmp_path):
    settings = SubjectSettings(faces_dir=str(tmp_path / "faces"), max_faces=2)
    service = SubjectService(session_factory, vector_store, settings)
    v = unit(1)
    first = await service.process_request(request(), [face(v, quality=0.1)])
    await service.process_request(request(), [face(with_similarity(v, 0.9, seed=5), quality=0.9)])
    await service.process_request(request(), [face(with_similarity(v, 0.9, seed=6), quality=0.5)])
    async with session_factory() as s:
        rows = (await s.execute(select(SubjectFace).order_by(SubjectFace.quality.desc()))).scalars().all()
        assert [r.quality for r in rows] == [0.9, 0.5]
        subject = await s.get(Subject, first.subject_id)
        assert subject.cover_face_id == rows[0].id
        assert subject.incident_count == 3
    files = list((tmp_path / "faces" / "subjects" / str(first.subject_id)).glob("*.jpg"))
    assert len(files) == 2
    assert len(vector_store.search(v, 20)) == 2


async def test_qdrant_failure_keeps_face_unsynced(session_factory, settings):
    service = SubjectService(session_factory, FailingStore(), settings)
    v = unit(1)
    first = await service.process_request(request(), [face(v)])
    second = await service.process_request(request(), [face(with_similarity(v, 0.8))])
    assert first.status == "matched"
    # exact search over Postgres embeddings still links the repeat
    assert second.match_type == "auto" and second.subject_id == first.subject_id
    async with session_factory() as s:
        assert (await s.execute(select(SubjectFace.qdrant_synced))).scalars().all() == [False, False]


async def test_hits_on_merged_subject_are_ignored(service, session_factory):
    v = unit(1)
    first = await service.process_request(request(), [face(v)])
    async with session_factory() as s:
        await s.execute(update(Subject).where(Subject.id == first.subject_id).values(status="merged"))
        await s.commit()
    second = await service.process_request(request(), [face(v)])
    assert second.match_type == "new"
    assert second.subject_id != first.subject_id


async def test_crop_write_failure_is_best_effort(service, session_factory, settings, vector_store, monkeypatch):
    req = request()
    incoming = Path(settings.faces_dir) / "incoming" / str(req.incident_id)
    incoming.mkdir(parents=True)
    (incoming / "00.jpg").write_bytes(b"x")

    def refuse(self, data):
        raise PermissionError(f"read-only: {self}")

    monkeypatch.setattr(Path, "write_bytes", refuse)
    v = unit(1)
    result = await service.process_request(req, [face(v)])
    assert result.status == "matched"
    assert not incoming.exists()
    async with session_factory() as s:
        row = (await s.execute(select(SubjectFace))).scalar_one()
        assert row.qdrant_synced is True
    assert vector_store.search(v, 1)[0].score > 0.99


async def test_index_step_failure_is_best_effort(service, settings, monkeypatch):
    req = request()
    incoming = Path(settings.faces_dir) / "incoming" / str(req.incident_id)
    incoming.mkdir(parents=True)

    async def broken(rows):
        raise RuntimeError("index step exploded")

    monkeypatch.setattr(service, "_index_faces", broken)
    result = await service.process_request(req, [face(unit(1))])
    assert result.status == "matched"
    assert not incoming.exists()


async def test_synced_flag_update_failure_is_swallowed(session_factory, vector_store, settings):
    calls = {"n": 0}

    def flaky_factory():
        calls["n"] += 1
        if calls["n"] == 2:  # the second session is _index_faces' synced-flag update
            raise ConnectionError("db blip")
        return session_factory()

    service = SubjectService(flaky_factory, vector_store, settings)
    result = await service.process_request(request(), [face(unit(1))])
    assert result.status == "matched"
    async with session_factory() as s:
        assert (await s.execute(select(SubjectFace.qdrant_synced))).scalar_one() is False

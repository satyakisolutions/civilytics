import os
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select, update

from app.db.models import FaceIncident, Subject, SubjectAccessLog, SubjectFace, SubjectMatchReview
from app.subjects.service import ReconcileResult, SubjectService

from tests.subjects.factories import FlakyStore, T0, face, request, unit, with_similarity


async def test_sweep_deletes_subjects_past_retention(service, session_factory):
    old = await service.process_request(request(at=T0), [face(unit(1))])
    fresh = await service.process_request(request(at=T0 + timedelta(days=80)), [face(unit(2))])
    result = await service.sweep(now=T0 + timedelta(days=95))
    assert result.subjects_deleted == 1
    async with session_factory() as s:
        assert await s.get(Subject, old.subject_id) is None
        assert await s.get(Subject, fresh.subject_id) is not None
        log = (await s.execute(select(SubjectAccessLog))).scalar_one()
        assert (log.actor, log.detail["reason"]) == ("system:retention", "retention")


async def test_sweep_removes_old_incoming_folders(service, settings):
    incoming = Path(settings.faces_dir) / "incoming"
    old_dir, new_dir = incoming / "old", incoming / "new"
    old_dir.mkdir(parents=True)
    new_dir.mkdir(parents=True)
    old_ts = (T0 - timedelta(days=10)).timestamp()
    os.utime(old_dir, (old_ts, old_ts))
    os.utime(new_dir, (T0.timestamp(), T0.timestamp()))
    result = await service.sweep(now=T0)
    assert result.incoming_dirs_deleted == 1
    assert not old_dir.exists() and new_dir.exists()


async def test_sweep_drops_old_no_face_rows(service, session_factory):
    req = request()
    await service.process_request(req, [])
    async with session_factory() as s:
        await s.execute(update(FaceIncident).values(created_at=T0 - timedelta(days=30)))
        await s.commit()
    result = await service.sweep(now=T0)
    assert result.no_face_deleted == 1
    async with session_factory() as s:
        assert await s.get(FaceIncident, req.incident_id) is None


async def test_sweep_drops_old_merged_tombstones(service, session_factory):
    v = unit(1)
    res_a = await service.process_request(request(), [face(v)])
    res_b = await service.process_request(request(), [face(with_similarity(v, 0.45))])
    async with session_factory() as s:
        review = (await s.execute(select(SubjectMatchReview))).scalar_one()
    await service.merge_review(review.id, actor="alice")
    async with session_factory() as s:
        await s.execute(update(Subject).where(Subject.id == res_b.subject_id).values(created_at=T0 - timedelta(days=100)))
        await s.commit()
    result = await service.sweep(now=T0)
    assert result.tombstones_deleted == 1
    async with session_factory() as s:
        assert await s.get(Subject, res_b.subject_id) is None
        assert await s.get(Subject, res_a.subject_id) is not None
        assert await s.get(SubjectMatchReview, review.id) is None


async def test_resync_indexes_unsynced_faces(session_factory, vector_store, settings):
    service = SubjectService(session_factory, FlakyStore(vector_store, fail_upserts=1), settings)
    v = unit(1)
    await service.process_request(request(), [face(v)])
    async with session_factory() as s:
        assert (await s.execute(select(SubjectFace.qdrant_synced))).scalar_one() is False
    assert await service.resync_index() == 1
    async with session_factory() as s:
        assert (await s.execute(select(SubjectFace.qdrant_synced))).scalar_one() is True
    assert vector_store.search(v, 1)[0].score > 0.99
    assert await service.resync_index() == 0


async def test_reconcile_deletes_orphan_points_and_files(service, session_factory, vector_store, settings):
    v = unit(1)
    res = await service.process_request(request(), [face(v)])
    async with session_factory() as s:
        kept = (await s.execute(select(SubjectFace))).scalar_one()
    subjects_dir = Path(settings.faces_dir) / "subjects"
    orphan_point = uuid.uuid4()
    vector_store.upsert(orphan_point, unit(2), uuid.uuid4(), uuid.uuid4())
    orphan_in_live_folder = subjects_dir / str(res.subject_id) / f"{uuid.uuid4()}.jpg"
    orphan_in_live_folder.write_bytes(b"\xff\xd8orphan")
    gone_subject = subjects_dir / str(uuid.uuid4())
    gone_subject.mkdir(parents=True)
    (gone_subject / f"{uuid.uuid4()}.jpg").write_bytes(b"\xff\xd8orphan")

    result = await service.reconcile()

    assert (result.qdrant_orphans_deleted, result.files_orphans_deleted) == (1, 2)
    assert vector_store.all_point_ids() == [kept.id]
    assert Path(kept.crop_path).exists()
    assert not orphan_in_live_folder.exists()
    assert not gone_subject.exists()
    assert (subjects_dir / str(res.subject_id)).is_dir()
    assert await service.reconcile() == ReconcileResult(0, 0)

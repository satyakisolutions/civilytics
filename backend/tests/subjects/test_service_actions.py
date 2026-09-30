import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, update

from app.db.models import (
    FaceIncident,
    Subject,
    SubjectAccessLog,
    SubjectFace,
    SubjectIncident,
    SubjectMatchReview,
)
from app.subjects.service import Conflict, NotFound

from tests.subjects.factories import T0, face, request, unit, with_similarity


async def _with_review(service):
    v = unit(1)
    req_a, req_b = request(), request()
    res_a = await service.process_request(req_a, [face(v)])
    res_b = await service.process_request(req_b, [face(with_similarity(v, 0.45))])
    async with service.session_factory() as s:
        review = (await s.execute(select(SubjectMatchReview))).scalar_one()
    return req_a, res_a, req_b, res_b, review, v


async def test_merge_folds_new_subject_into_candidate(service, session_factory, vector_store):
    req_a, res_a, req_b, res_b, review, v = await _with_review(service)
    target = await service.merge_review(review.id, actor="alice", client_ip="10.0.0.5")
    assert target == res_a.subject_id
    async with session_factory() as s:
        src = await s.get(Subject, res_b.subject_id)
        dst = await s.get(Subject, res_a.subject_id)
        assert src.status == "merged" and src.merged_into_id == dst.id
        assert dst.incident_count == 2
        inc = await s.get(SubjectIncident, req_b.incident_id)
        assert inc.subject_id == dst.id and inc.match_type == "review_merged"
        decided = await s.get(SubjectMatchReview, review.id)
        assert decided.status == "merged" and decided.decided_by == "alice"
        log = (await s.execute(select(SubjectAccessLog))).scalar_one()
        assert (log.action, log.actor, log.client_ip, log.subject_id) == ("merge", "alice", "10.0.0.5", dst.id)
    hits = vector_store.search(with_similarity(v, 0.45), 20)
    assert {h.subject_id for h in hits} == {res_a.subject_id}


async def test_merge_twice_conflicts(service):
    *_, review, _ = await _with_review(service)
    await service.merge_review(review.id, actor="alice")
    with pytest.raises(Conflict):
        await service.merge_review(review.id, actor="bob")


async def test_merge_when_source_no_longer_active_conflicts(service, session_factory):
    _, _, _, res_b, review, _ = await _with_review(service)
    async with session_factory() as s:
        await s.execute(update(Subject).where(Subject.id == res_b.subject_id).values(status="merged"))
        await s.commit()
    with pytest.raises(Conflict):
        await service.merge_review(review.id, actor="alice")
    async with session_factory() as s:
        assert (await s.get(SubjectMatchReview, review.id)).status == "pending"


async def test_merge_after_candidate_deleted_is_not_found(service):
    _, res_a, _, _, review, _ = await _with_review(service)
    await service.delete_subject(res_a.subject_id, actor="alice")
    with pytest.raises(NotFound):
        await service.merge_review(review.id, actor="alice")


async def test_reject_keeps_both_subjects(service, session_factory):
    _, res_a, _, res_b, review, _ = await _with_review(service)
    await service.reject_review(review.id, actor="alice")
    async with session_factory() as s:
        assert (await s.get(SubjectMatchReview, review.id)).status == "rejected"
        assert (await s.get(Subject, res_a.subject_id)).status == "active"
        assert (await s.get(Subject, res_b.subject_id)).status == "active"
        log = (await s.execute(select(SubjectAccessLog))).scalar_one()
        assert (log.action, log.subject_id) == ("reject", res_b.subject_id)
    with pytest.raises(Conflict):
        await service.reject_review(review.id, actor="alice")


async def test_detach_moves_incident_to_new_subject(service, session_factory, vector_store):
    v = unit(1)
    req_a, req_c = request(), request(at=T0 + timedelta(days=1))
    res_a = await service.process_request(req_a, [face(v)])
    vc = with_similarity(v, 0.7)
    await service.process_request(req_c, [face(vc)])
    new_id = await service.detach_incident(res_a.subject_id, req_c.incident_id, actor="alice")
    async with session_factory() as s:
        old = await s.get(Subject, res_a.subject_id)
        new = await s.get(Subject, new_id)
        assert (old.incident_count, new.incident_count, new.code) == (1, 1, "S-002")
        inc = await s.get(SubjectIncident, req_c.incident_id)
        assert inc.subject_id == new_id and inc.match_type == "detached"
        log = (await s.execute(select(SubjectAccessLog))).scalar_one()
        assert log.action == "detach" and log.detail["new_subject_id"] == str(new_id)
    assert vector_store.search(vc, 1)[0].subject_id == new_id


async def test_detach_only_incident_conflicts(service):
    req = request()
    res = await service.process_request(req, [face(unit(1))])
    with pytest.raises(Conflict):
        await service.detach_incident(res.subject_id, req.incident_id, actor="alice")


async def test_detach_wrong_subject_is_not_found(service):
    req = request()
    await service.process_request(req, [face(unit(1))])
    with pytest.raises(NotFound):
        await service.detach_incident(uuid.uuid4(), req.incident_id, actor="alice")


async def test_delete_subject_removes_everything_but_the_log(service, session_factory, vector_store, settings):
    req = request()
    res = await service.process_request(req, [face(unit(1))])
    await service.delete_subject(res.subject_id, actor="alice")
    async with session_factory() as s:
        assert await s.get(Subject, res.subject_id) is None
        assert (await s.execute(select(func.count()).select_from(SubjectFace))).scalar_one() == 0
        assert (await s.execute(select(func.count()).select_from(SubjectIncident))).scalar_one() == 0
        assert (await s.get(FaceIncident, req.incident_id)).status == "deleted"
        log = (await s.execute(select(SubjectAccessLog))).scalar_one()
        assert (log.action, log.subject_id, log.detail["reason"]) == ("delete", res.subject_id, "operator")
    assert vector_store.search(unit(1), 20) == []
    assert not (Path(settings.faces_dir) / "subjects" / str(res.subject_id)).exists()


async def test_delete_unknown_subject_is_not_found(service):
    with pytest.raises(NotFound):
        await service.delete_subject(uuid.uuid4(), actor="alice")


async def test_delete_subject_keeps_detached_subjects_crop_files(service, session_factory):
    v = unit(1)
    req_a, req_c = request(), request()
    res_a = await service.process_request(req_a, [face(v)])
    await service.process_request(req_c, [face(with_similarity(v, 0.7))])
    new_id = await service.detach_incident(res_a.subject_id, req_c.incident_id, actor="alice")
    async with session_factory() as s:
        old_face = (await s.execute(
            select(SubjectFace).where(SubjectFace.subject_id == res_a.subject_id)
        )).scalar_one()
        new_face = (await s.execute(
            select(SubjectFace).where(SubjectFace.subject_id == new_id)
        )).scalar_one()
    old_path, new_path = Path(old_face.crop_path), Path(new_face.crop_path)
    assert old_path.exists() and new_path.exists()

    await service.delete_subject(res_a.subject_id, actor="alice")

    assert not old_path.exists()
    assert new_path.exists()


async def test_merge_does_not_repoint_non_pending_reviews(service, session_factory):
    v = unit(1)
    req_a, req_b = request(), request()
    res_a = await service.process_request(req_a, [face(v)])
    res_b = await service.process_request(req_b, [face(with_similarity(v, 0.45))])
    async with session_factory() as s:
        review1 = (await s.execute(select(SubjectMatchReview))).scalar_one()
    await service.reject_review(review1.id, actor="alice")

    # S-003: a subject unrelated to v/S-002, so it forms without a review.
    req_d = request()
    res_d = await service.process_request(req_d, [face(unit(42))])

    # Manually stand up a second, still-pending review with the same source
    # subject (S-002) as review1, so merging it exercises the repoint.
    async with session_factory() as s:
        review2 = SubjectMatchReview(
            incident_id=uuid.uuid4(), new_subject_id=res_b.subject_id,
            candidate_subject_id=res_d.subject_id, similarity=0.5, status="pending",
        )
        s.add(review2)
        await s.commit()
        await s.refresh(review2)

    await service.merge_review(review2.id, actor="alice")

    async with session_factory() as s:
        rejected = await s.get(SubjectMatchReview, review1.id)
        assert rejected.status == "rejected"
        assert rejected.new_subject_id == res_b.subject_id


async def test_delete_subject_file_errors_are_best_effort(service, session_factory, monkeypatch):
    res = await service.process_request(request(), [face(unit(1))])

    def refuse(self, missing_ok=False):
        raise PermissionError(f"cannot unlink {self}")

    monkeypatch.setattr(Path, "unlink", refuse)
    await service.delete_subject(res.subject_id, actor="tester")
    async with session_factory() as s:
        assert await s.get(Subject, res.subject_id) is None

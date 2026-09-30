import uuid
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.models import Alert, AlertSeverity, AlertStatus, SubjectAccessLog, SubjectMatchReview

from tests.subjects.factories import T0, face, request, unit, with_similarity


@pytest.fixture
async def subjects_client(app, service):
    from app.api.routes.subjects import get_subject_service

    app.dependency_overrides[get_subject_service] = lambda: service
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _repeat_subject(service, session_factory):
    v = unit(1)
    req1 = request(camera_id="cam-01", at=T0)
    req2 = request(camera_id="cam-02", at=T0 + timedelta(days=2))
    res1 = await service.process_request(req1, [face(v)])
    await service.process_request(req2, [face(with_similarity(v, 0.8))])
    async with session_factory() as s:
        s.add(Alert(severity=AlertSeverity.critical, title="Waste dumping confirmed", zone="Zone A",
                    status=AlertStatus.active, incident_id=req1.incident_id,
                    snapshot_url="/evidence/alerts/cam-01/a.jpg"))
        await s.commit()
    return res1.subject_id, req1, req2


async def test_list_empty(subjects_client):
    resp = await subjects_client.get("/api/subjects")
    assert resp.status_code == 200 and resp.json() == []


async def test_list_and_detail(subjects_client, service, session_factory):
    subject_id, req1, _ = await _repeat_subject(service, session_factory)
    [item] = (await subjects_client.get("/api/subjects")).json()
    assert (item["code"], item["incident_count"], item["camera_ids"]) == ("S-001", 2, ["cam-01", "cam-02"])
    detail = (await subjects_client.get(f"/api/subjects/{subject_id}")).json()
    assert len(detail["faces"]) == 2 and len(detail["incidents"]) == 2
    first = next(i for i in detail["incidents"] if i["incident_id"] == str(req1.incident_id))
    assert (first["snapshot_url"], first["zone"], first["match_type"]) == (
        "/evidence/alerts/cam-01/a.jpg", "Zone A", "new",
    )


async def test_repeat_only_filter(subjects_client, service, session_factory):
    await _repeat_subject(service, session_factory)
    await service.process_request(request(), [face(unit(7))])
    codes = [s["code"] for s in (await subjects_client.get("/api/subjects?repeat_only=true")).json()]
    assert codes == ["S-001"]


async def test_face_image_served_privately(subjects_client, service, session_factory):
    subject_id, _, _ = await _repeat_subject(service, session_factory)
    detail = (await subjects_client.get(f"/api/subjects/{subject_id}")).json()
    resp = await subjects_client.get(f"/api/subjects/faces/{detail['faces'][0]['id']}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.headers["cache-control"] == "no-store"
    assert resp.content.startswith(b"\xff\xd8")
    assert (await subjects_client.get(f"/api/subjects/faces/{uuid.uuid4()}")).status_code == 404


async def test_unknown_subject_is_404(subjects_client):
    assert (await subjects_client.get(f"/api/subjects/{uuid.uuid4()}")).status_code == 404


async def test_merge_route_conflict_is_409(subjects_client, service, session_factory):
    v = unit(1)
    a = await service.process_request(request(), [face(v)])
    await service.process_request(request(), [face(with_similarity(v, 0.45))])
    [review] = (await subjects_client.get("/api/subjects/reviews")).json()
    assert review["candidate"]["code"] == "S-001" and review["new_subject"]["code"] == "S-002"
    assert len(review["new_subject"]["face_ids"]) == 1
    ok = await subjects_client.post(f"/api/subjects/reviews/{review['id']}/merge")
    assert ok.status_code == 200 and ok.json()["subject_id"] == str(a.subject_id)
    again = await subjects_client.post(f"/api/subjects/reviews/{review['id']}/merge")
    assert again.status_code == 409
    assert (await subjects_client.get("/api/subjects/reviews")).json() == []


async def test_review_count_is_not_logged(subjects_client, service, session_factory):
    v = unit(1)
    await service.process_request(request(), [face(v)])
    await service.process_request(request(), [face(with_similarity(v, 0.45))])
    resp = await subjects_client.get("/api/subjects/reviews/count")
    assert resp.status_code == 200 and resp.json() == {"pending": 1}
    async with session_factory() as s:
        assert (await s.execute(select(SubjectAccessLog))).scalars().all() == []


async def test_reject_route(subjects_client, service, session_factory):
    v = unit(1)
    await service.process_request(request(), [face(v)])
    await service.process_request(request(), [face(with_similarity(v, 0.45))])
    [review] = (await subjects_client.get("/api/subjects/reviews")).json()
    assert (await subjects_client.post(f"/api/subjects/reviews/{review['id']}/reject")).status_code == 200
    async with session_factory() as s:
        assert (await s.get(SubjectMatchReview, uuid.UUID(review["id"]))).status == "rejected"


async def test_detach_route(subjects_client, service, session_factory):
    subject_id, _, req2 = await _repeat_subject(service, session_factory)
    resp = await subjects_client.post(f"/api/subjects/{subject_id}/incidents/{req2.incident_id}/detach")
    assert resp.status_code == 200
    new_id = resp.json()["subject_id"]
    assert (await subjects_client.get(f"/api/subjects/{new_id}")).json()["code"] == "S-002"
    only = await subjects_client.post(f"/api/subjects/{new_id}/incidents/{req2.incident_id}/detach")
    assert only.status_code == 409


async def test_delete_route(subjects_client, service, session_factory):
    subject_id, _, _ = await _repeat_subject(service, session_factory)
    assert (await subjects_client.delete(f"/api/subjects/{subject_id}")).status_code == 200
    assert (await subjects_client.get(f"/api/subjects/{subject_id}")).status_code == 404
    assert (await subjects_client.delete(f"/api/subjects/{subject_id}")).status_code == 404


async def test_access_log_records_reads(subjects_client, service, session_factory):
    subject_id, _, _ = await _repeat_subject(service, session_factory)
    await subjects_client.get("/api/subjects")
    detail = (await subjects_client.get(f"/api/subjects/{subject_id}")).json()
    await subjects_client.get(f"/api/subjects/faces/{detail['faces'][0]['id']}")
    async with session_factory() as s:
        rows = (await s.execute(select(SubjectAccessLog))).scalars().all()
    assert {r.action for r in rows} == {"list", "view", "face_view"}
    assert {r.actor for r in rows} == {"dev"}


async def test_requires_admin_without_dev_mode(subjects_client, monkeypatch):
    monkeypatch.delenv("WW_AUTH_DEV_MODE", raising=False)
    assert (await subjects_client.get("/api/subjects")).status_code == 401


async def test_detail_one_row_per_incident_with_earliest_alert(service, session_factory):
    """Several alerts can share one incident_id (per-track episode): one incident row, earliest alert wins."""
    from app.subjects.queries import subject_detail

    req = request(camera_id="cam-01", at=T0)
    res = await service.process_request(req, [face(unit(1))])
    async with session_factory() as s:
        s.add(Alert(severity=AlertSeverity.critical, title="Reminder", zone="Zone B",
                    status=AlertStatus.active, incident_id=req.incident_id,
                    snapshot_url="/evidence/alerts/cam-01/later.jpg", timestamp=T0 + timedelta(seconds=37)))
        s.add(Alert(severity=AlertSeverity.critical, title="Waste dumping confirmed", zone="Zone A",
                    status=AlertStatus.active, incident_id=req.incident_id,
                    snapshot_url="/evidence/alerts/cam-01/first.jpg", timestamp=T0))
        s.add(Alert(severity=AlertSeverity.critical, title="Violation confirmed", zone="Zone C",
                    status=AlertStatus.active, incident_id=req.incident_id,
                    snapshot_url="/evidence/alerts/cam-01/second.jpg", timestamp=T0 + timedelta(seconds=5)))
        await s.commit()
        detail = await subject_detail(s, res.subject_id)
    [incident] = detail["incidents"]
    assert (incident["snapshot_url"], incident["zone"], incident["title"]) == (
        "/evidence/alerts/cam-01/first.jpg", "Zone A", "Waste dumping confirmed",
    )

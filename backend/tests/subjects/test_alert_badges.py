import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.models import Alert, AlertSeverity, AlertStatus
from app.subjects.queries import alert_badges

from tests.subjects.factories import face, request, unit, with_similarity


async def test_badges_for_each_state(service, session_factory):
    v = unit(1)
    pending, no_face = request(), request()
    first, repeat, uncertain = request(), request(), request()
    await service.process_request(no_face, [])
    await service.process_request(first, [face(v)])
    await service.process_request(repeat, [face(with_similarity(v, 0.8))])
    # own seed: sharing seed 999 with the 0.8 face would make it ~0.9 similar to that face
    await service.process_request(uncertain, [face(with_similarity(v, 0.45, seed=7))])
    ids = [r.incident_id for r in (pending, no_face, first, repeat, uncertain)]
    async with session_factory() as s:
        badges = await alert_badges(s, ids)
    assert pending.incident_id not in badges
    assert badges[no_face.incident_id].face_status == "no_face"
    assert badges[no_face.incident_id].subject_code is None
    assert (badges[first.incident_id].subject_code, badges[first.incident_id].subject_incident_count) == ("S-001", 2)
    assert badges[repeat.incident_id].subject_code == "S-001"
    assert badges[first.incident_id].subject_review_pending is False
    assert badges[uncertain.incident_id].subject_code == "S-002"
    assert badges[uncertain.incident_id].subject_review_pending is True


async def test_badges_empty_input(session_factory):
    async with session_factory() as s:
        assert await alert_badges(s, []) == {}


@pytest.fixture
async def alerts_client(app, session_factory):
    from app.api.deps import get_db

    async def override():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def test_list_alerts_includes_subject_badge(alerts_client, session_factory, service):
    req = request()
    await service.process_request(req, [face(unit(1))])
    async with session_factory() as s:
        s.add(Alert(severity=AlertSeverity.critical, title="Waste dumping confirmed",
                    status=AlertStatus.active, incident_id=req.incident_id))
        s.add(Alert(severity=AlertSeverity.warning, title="Older alert", status=AlertStatus.active))
        await s.commit()
    resp = await alerts_client.get("/api/alerts")
    assert resp.status_code == 200
    by_title = {a["title"]: a for a in resp.json()}
    a = by_title["Waste dumping confirmed"]
    assert a["incident_id"] == str(req.incident_id)
    assert (a["face_status"], a["subject_code"], a["subject_incident_count"], a["subject_review_pending"]) == (
        "matched", "S-001", 1, False,
    )
    old = by_title["Older alert"]
    assert old["face_status"] is None and old["subject_code"] is None


async def test_alert_consumer_stores_incident_id(monkeypatch):
    from app.services import alert_consumer as ac

    captured: dict = {}

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, stmt):
            captured["params"] = stmt.compile().params

        async def commit(self):
            pass

    broadcasts: list[dict] = []

    async def fake_broadcast(payload):
        broadcasts.append(payload)

    async def fake_uuid(name):
        return "11111111-1111-1111-1111-111111111111"

    async def fake_info(name):
        return None

    monkeypatch.setattr(ac, "async_session", lambda: FakeSession())
    monkeypatch.setattr(ac, "broadcast_alert", fake_broadcast)
    consumer = ac.AlertConsumer("kafka:9092")
    monkeypatch.setattr(consumer, "_resolve_camera_uuid", fake_uuid)
    monkeypatch.setattr(consumer, "_resolve_camera_info", fake_info)

    iid = str(uuid.uuid4())
    await consumer._handle_alert({
        "camera_id": "cam-01", "severity": "critical", "title": "t", "message": "m",
        "suppress_tts": True, "incident_id": iid, "detected_at": 1790000000.0,
    })
    assert str(captured["params"]["incident_id"]) == iid
    assert broadcasts[0]["incident_id"] == iid

    await consumer._handle_alert({
        "camera_id": "cam-01", "severity": "critical", "title": "t", "message": "m",
        "suppress_tts": True, "incident_id": "garbage",
    })
    assert captured["params"]["incident_id"] is None


async def test_list_alerts_face_result_timeout(alerts_client, session_factory):
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    async with session_factory() as s:
        s.add(Alert(severity=AlertSeverity.critical, title="young", status=AlertStatus.active,
                    incident_id=uuid.uuid4(), timestamp=now - timedelta(seconds=10)))
        s.add(Alert(severity=AlertSeverity.critical, title="old", status=AlertStatus.active,
                    incident_id=uuid.uuid4(), timestamp=now - timedelta(seconds=200)))
        s.add(Alert(severity=AlertSeverity.warning, title="old without incident", status=AlertStatus.active,
                    timestamp=now - timedelta(seconds=200)))
        await s.commit()
    by_title = {a["title"]: a for a in (await alerts_client.get("/api/alerts")).json()}
    assert by_title["young"]["face_status"] is None
    assert by_title["old"]["face_status"] == "no_face"
    assert by_title["old"]["subject_code"] is None
    assert by_title["old without incident"]["face_status"] is None


def test_face_result_timed_out_handles_naive_and_aware():
    from datetime import datetime, timedelta, timezone

    from app.api.routes.alerts import FACE_RESULT_TIMEOUT_S, _face_result_timed_out

    assert FACE_RESULT_TIMEOUT_S == 120
    now = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    old_aware = now - timedelta(seconds=121)
    young_aware = now - timedelta(seconds=119)
    assert _face_result_timed_out(old_aware, now) is True
    assert _face_result_timed_out(young_aware, now) is False
    assert _face_result_timed_out(old_aware.replace(tzinfo=None), now) is True
    assert _face_result_timed_out(young_aware.replace(tzinfo=None), now) is False
    ist = timezone(timedelta(hours=5, minutes=30))
    assert _face_result_timed_out(young_aware.astimezone(ist), now) is False
    assert _face_result_timed_out(None, now) is False

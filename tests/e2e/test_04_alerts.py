"""Step 4 — alerts flowing.

Publish a synthetic alert to `wastewatch.alerts` for our camera; AlertConsumer
should broadcast it on /ws/alerts AND persist a row that GET /api/alerts
returns.

Fixed 2026-10-13 (live stack): AlertConsumer._handle_alert previously inserted
`.values(zone=<str>)` against the `alerts` table, whose column is `zone_id`
(UUID FK) — every write raised and was swallowed, and the tenant_id landed
NULL so even once the bogus `zone` key was dropped, tenant-scoped GET
/api/alerts never saw the rows. Alerts now insert with the camera's tenant_id
and the AlertResponse.zone field is optional. Both the WS and DB assertions
are real guards.
"""
import time

import pytest

from helpers import publish, collect_ws, kafka_reachable

pytestmark = pytest.mark.e2e

TOPIC_ALERTS = "wastewatch.alerts"


@pytest.fixture
def _kafka(stack):
    if not kafka_reachable(stack.kafka):
        pytest.skip(f"kafka not reachable at {stack.kafka}")
    return stack.kafka


def _alert(camera_id: str, title: str) -> dict:
    return {
        "camera_id": camera_id,
        "severity": "critical",
        "title": title,
        "message": "synthetic dumping detected",
        "track_id": 1,
        "suppress_tts": True,          # no TTS worker dependency
        "snapshot_url": None,
        "detected_at": time.time(),
    }


def test_alert_broadcast_on_ws(_kafka, stack, make_camera, ws_token):
    import threading

    cam = make_camera()
    title = f"E2E Alert {cam['id'][:8]}"
    got: list = []

    def _listen():
        got.extend(collect_ws("/alerts", ws_token, stack, duration=12,
                              match_type="alert"))

    t = threading.Thread(target=_listen, daemon=True)
    t.start()
    time.sleep(2)

    publish(TOPIC_ALERTS, _alert(cam["id"], title), _kafka)
    t.join(timeout=14)

    titles = [m.get("title") for m in got if m.get("type") == "alert"]
    assert title in titles, f"alert not broadcast on /ws/alerts (got {titles})"


def test_alert_persisted_to_api(_kafka, stack, client, make_camera):
    cam = make_camera()
    title = f"E2E Persist {cam['id'][:8]}"
    publish(TOPIC_ALERTS, _alert(cam["id"], title), _kafka)

    for _ in range(40):
        rows = client.get(f"/api/alerts?camera_id={cam['id']}").json()
        if rows:
            assert rows[0]["title"] == title
            return
        time.sleep(0.3)
    pytest.fail("alert never appeared via GET /api/alerts")

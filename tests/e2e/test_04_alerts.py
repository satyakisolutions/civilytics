"""Step 4 — alerts flowing.

Publish a synthetic alert to `wastewatch.alerts` for our camera; AlertConsumer
should broadcast it on /ws/alerts (and, in a healthy stack, persist a row that
GET /api/alerts returns).

Product bug found (2026-10-13, live stack): AlertConsumer._handle_alert inserts
`.values(zone=<str>)` but the `alerts` table column is `zone_id` (UUID FK to
zones). The insert raises ("Failed to save alert to DB"), so NO pipeline/worker
alert is ever persisted — alerts exist only transiently over WebSocket, and
GET /api/alerts returns them for no camera. The DB/API assertion is therefore
xfail until that is fixed; the WS assertion is the real guard.
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


@pytest.mark.xfail(reason="AlertConsumer inserts zone= (str) vs alerts.zone_id "
                          "(UUID) — insert raises, no alert row persists",
                   strict=False)
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

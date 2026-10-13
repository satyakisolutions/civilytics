"""Step 10 — latency metrics (measure + guard; prints measured values).

  * frame latency     : event source-ts → observed on /ws/events
  * alert latency     : alert publish  → observed on /ws/alerts
  (dashboard update latency is measured in the Playwright test, step 5.)

Thresholds are generous and env-tunable (E2E_FRAME_LATENCY_MS,
E2E_ALERT_LATENCY_S). The goal is regression-guarding, not tight SLOs.
"""
import threading
import time
import uuid

import pytest

from helpers import publish, collect_ws, kafka_reachable

pytestmark = [pytest.mark.e2e, pytest.mark.latency]


@pytest.fixture
def _kafka(stack):
    if not kafka_reachable(stack.kafka):
        pytest.skip(f"kafka not reachable at {stack.kafka}")
    return stack.kafka


def test_frame_latency(_kafka, stack, make_camera, ws_token, capsys):
    cam = make_camera()
    frames: list = []
    track = int(uuid.uuid4().int % 100000)

    def _listen():
        frames.extend(collect_ws(
            "/events", ws_token, stack, duration=12,
            stop_when=lambda frame: (
                frame.get("state") == "VIOLATION"
                and frame.get("camera_id") == cam["id"]
                and frame.get("track_id") == track
            ),
        ))

    t = threading.Thread(target=_listen, daemon=True); t.start()
    time.sleep(2)

    src_ts = time.time()
    publish("wastewatch.events", {
        "camera_id": cam["id"], "track_id": track, "state": "VIOLATION",
        "prev_state": "ACTIVE", "dwell_time_s": 1.0, "class_name": "person",
        "bbox": [0.1, 0.1, 0.2, 0.2], "timestamp": src_ts,
    }, _kafka)
    recv_wall = None
    t.join(timeout=14)
    matching = [
        f for f in frames
        if f.get("state") == "VIOLATION"
        and f.get("camera_id") == cam["id"]
        and f.get("track_id") == track
    ]
    assert matching, "no matching event frame observed"
    # The listener exits immediately on the matching message, making this a
    # tight upper bound without changing the production WebSocket protocol.
    recv_wall = time.time()
    latency_ms = (recv_wall - src_ts) * 1000
    with capsys.disabled():
        print(f"\n[latency] frame source-ts → ws/events: {latency_ms:.0f} ms")
    assert latency_ms < stack.frame_latency_ms, \
        f"frame latency {latency_ms:.0f}ms over {stack.frame_latency_ms}ms"


def test_alert_latency(_kafka, stack, make_camera, ws_token, capsys):
    cam = make_camera()
    title = f"E2E Lat {cam['id'][:8]}"
    got: list = []

    def _listen():
        got.extend(collect_ws("/alerts", ws_token, stack, duration=12, match_type="alert"))

    t = threading.Thread(target=_listen, daemon=True); t.start()
    time.sleep(2)

    t0 = time.time()
    publish("wastewatch.alerts", {
        "camera_id": cam["id"], "severity": "critical", "title": title,
        "message": "m", "track_id": 1, "suppress_tts": True,
        "detected_at": t0,
    }, _kafka)
    t.join(timeout=14)
    t1 = time.time()
    assert any(m.get("title") == title for m in got), "alert not observed on ws"
    latency_s = t1 - t0
    with capsys.disabled():
        print(f"\n[latency] alert publish → ws/alerts: {latency_s:.2f} s")
    assert latency_s < stack.alert_latency_s, \
        f"alert latency {latency_s:.2f}s over {stack.alert_latency_s}s"

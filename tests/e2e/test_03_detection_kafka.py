"""Step 3 — detection via Kafka.

Publish a synthetic pipeline detection to `wastewatch.events` and assert it
flows through EventConsumer onto the /ws/events socket.

Product note: the pipeline event consumer is WebSocket-only — it does NOT
write a violation_events row (only the full DeepStream+Conductor path does),
so GET /api/events stays empty for synthetic injection. Asserting the WS push
is the faithful check for "a detection flowed".
"""
import time
import uuid

import pytest

from helpers import publish, collect_ws, kafka_reachable

pytestmark = pytest.mark.e2e

TOPIC_EVENTS = "wastewatch.events"


@pytest.fixture
def _kafka(stack):
    if not kafka_reachable(stack.kafka):
        pytest.skip(f"kafka not reachable at {stack.kafka}")
    return stack.kafka


def _detection(camera_id: str) -> dict:
    return {
        "camera_id": camera_id,
        "track_id": int(uuid.uuid4().int % 100000),
        "state": "VIOLATION",
        "prev_state": "ACTIVE",
        "dwell_time_s": 5.0,
        "class_name": "person",
        "bbox": [0.1, 0.1, 0.2, 0.2],
        "timestamp": time.time(),
    }


def test_detection_reaches_events_socket(_kafka, stack, make_camera, ws_token):
    import threading

    cam = make_camera()
    frames: list = []

    def _listen():
        frames.extend(collect_ws("/events", ws_token, stack, duration=12,
                                 match_type=None))

    t = threading.Thread(target=_listen, daemon=True)
    t.start()
    time.sleep(2)  # let the socket subscribe

    publish(TOPIC_EVENTS, _detection(cam["id"]), _kafka)
    t.join(timeout=14)

    states = [f.get("state") for f in frames]
    assert "VIOLATION" in states, f"no VIOLATION event on /ws/events (got {frames[:3]})"

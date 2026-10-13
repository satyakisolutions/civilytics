"""Step 7 — analytics counts reflect data and are well-formed."""
import pytest

pytestmark = pytest.mark.e2e

_SUMMARY_KEYS = {
    "total_events", "events_today", "events_this_week", "critical_today",
    "cameras_online", "cameras_total", "resolved_events", "pending_events",
    "repeat_offenders",
}


def test_summary_shape(client):
    r = client.get("/api/analytics/summary")
    assert r.status_code == 200
    data = r.json()
    assert _SUMMARY_KEYS <= set(data)
    for k in _SUMMARY_KEYS:
        assert data[k] is None or isinstance(data[k], (int, float))


def test_cameras_total_reflects_created(client, make_camera):
    before = client.get("/api/analytics/summary").json()["cameras_total"]
    make_camera()
    after = client.get("/api/analytics/summary").json()["cameras_total"]
    assert after >= before + 1


def test_analytics_endpoints_ok(client):
    for path in ("/api/analytics/hourly", "/api/analytics/daily",
                 "/api/analytics/by-zone", "/api/analytics/by-camera",
                 "/api/analytics/heatmap"):
        assert client.get(path).status_code == 200, path

"""Step 6 — evidence artifacts.

On the live stack with synthetic injection (no GPU pipeline) there is no real
evidence file produced, so this asserts the evidence API is reachable and
well-formed, and that the path-traversal guard on /api/evidence/file holds.
If any evidence rows exist, assert their shape. Best-effort by design.
"""
import pytest

pytestmark = pytest.mark.e2e


def test_evidence_listing_reachable(client):
    r = client.get("/api/evidence/files?limit=5")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_evidence_by_event_shape(client, make_camera):
    cam = make_camera()
    r = client.get(f"/api/evidence?event_id={cam['id']}")
    assert r.status_code == 200
    rows = r.json()
    assert isinstance(rows, list)
    for row in rows:
        assert {"id", "event_id", "file_type", "sync_status"} <= set(row)


def test_evidence_file_path_guard(client):
    # Absolute path outside EVIDENCE_ROOT must be rejected, not served.
    r = client.get("/api/evidence/file", params={"path": "/etc/passwd"})
    assert r.status_code in (403, 404)

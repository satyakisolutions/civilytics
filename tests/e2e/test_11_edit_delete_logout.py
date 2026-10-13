"""Step 11 — edit camera, delete camera, logout."""
import httpx
import pytest
import uuid

pytestmark = pytest.mark.e2e


def test_edit_camera(client, make_camera):
    cam = make_camera(zone="Zone-Before")
    r = client.patch(f"/api/cameras/{cam['id']}", json={"zone": "Zone-After",
                                                        "location": "Edited Loc"})
    assert r.status_code == 200
    assert r.json()["zone"] == "Zone-After"
    assert client.get(f"/api/cameras/{cam['id']}").json()["location"] == "Edited Loc"


def test_delete_camera(client):
    cam = client.post("/api/cameras", json={
        "name": f"E2E-delete-{uuid.uuid4().hex[:8]}", "zone": "Z", "source_type": "rtsp",
    }).json()
    assert client.delete(f"/api/cameras/{cam['id']}").status_code == 204
    assert client.get(f"/api/cameras/{cam['id']}").status_code == 404


def test_logout_clears_session(stack):
    c = httpx.Client(base_url=stack.base_url, verify=stack.verify_tls, timeout=30)
    c.post("/api/auth/login", json={"username": stack.username, "password": stack.password})
    assert c.get("/api/auth/me").status_code == 200

    assert c.post("/api/auth/logout").status_code == 204
    # Cookies cleared → /me is now unauthorized.
    assert c.get("/api/auth/me").status_code == 401
    c.close()

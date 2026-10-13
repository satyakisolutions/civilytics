"""Step 1 — onboard a camera with a location (incl. Maps-link-derived latlng)."""
import pytest

from helpers import parse_google_maps_latlng

pytestmark = pytest.mark.e2e


def test_create_camera_with_latlng(client, make_camera):
    cam = make_camera(gps_lat=22.5726, gps_lng=88.3639)
    assert cam["gps_lat"] == pytest.approx(22.5726)
    assert cam["gps_lng"] == pytest.approx(88.3639)

    got = client.get(f"/api/cameras/{cam['id']}")
    assert got.status_code == 200
    assert got.json()["id"] == cam["id"]

    listed = client.get("/api/cameras").json()
    assert any(c["id"] == cam["id"] for c in listed)


def test_create_camera_from_google_maps_link(client, make_camera):
    # No backend Maps parser exists; we port the frontend parser and feed the
    # derived latlng to create (exercises the real onboarding input shape).
    url = "https://www.google.com/maps/place/Kolkata/@22.5726,88.3639,15z"
    latlng = parse_google_maps_latlng(url)
    assert latlng == {"lat": 22.5726, "lng": 88.3639}

    cam = make_camera(gps_lat=latlng["lat"], gps_lng=latlng["lng"])
    assert client.get(f"/api/cameras/{cam['id']}").json()["gps_lat"] == pytest.approx(22.5726)

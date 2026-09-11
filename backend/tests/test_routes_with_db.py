"""Route tests with mocked database dependency."""
import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import ASGITransport, AsyncClient


def _make_mock_camera(cam_id=None, name="Test Cam", zone="Zone A", status="online"):
    cam = MagicMock()
    cam.id = cam_id or uuid.uuid4()
    cam.name = name
    cam.zone = zone
    cam.status = status
    cam.is_pinned = False
    cam.stream_url = "rtsp://test"
    cam.roi_polygons = None
    cam.recording_config = None
    cam.location = "Test"
    cam.gps = None
    cam.created_at = None
    cam.updated_at = None
    return cam


@pytest.fixture
def mock_db():
    return AsyncMock()


@pytest.fixture
def app_with_mock_db(app, mock_db):
    from app.api.deps import get_db
    async def override():
        yield mock_db
    app.dependency_overrides[get_db] = override
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
async def mclient(app_with_mock_db):
    async with AsyncClient(
        transport=ASGITransport(app=app_with_mock_db, raise_app_exceptions=False),
        base_url="http://test",
    ) as ac:
        yield ac


# ── Cameras ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_cameras_empty(mclient, mock_db):
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = mock_result
    resp = await mclient.get("/api/cameras")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_camera_not_found(mclient, mock_db):
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result
    resp = await mclient.get(f"/api/cameras/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_camera_not_found(mclient, mock_db):
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result
    resp = await mclient.delete(f"/api/cameras/{uuid.uuid4()}")
    assert resp.status_code == 404


# ── ROI ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_roi_not_found(mclient, mock_db):
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result
    resp = await mclient.get(f"/api/cameras/{uuid.uuid4()}/roi")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_roi_with_polygons(mclient, mock_db):
    cam = _make_mock_camera()
    cam.roi_polygons = {"polygons": [{"name": "zone1", "points": [{"x": 0.1, "y": 0.2}, {"x": 0.5, "y": 0.3}, {"x": 0.3, "y": 0.8}]}]}
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = cam
    mock_db.execute.return_value = mock_result
    resp = await mclient.get(f"/api/cameras/{cam.id}/roi")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["polygons"]) == 1
    assert data["polygons"][0]["bounding_rect"] is not None
    assert data["merged_crop_rect"] is not None


@pytest.mark.asyncio
async def test_clear_roi(mclient, mock_db):
    cam = _make_mock_camera()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = cam
    mock_db.execute.return_value = mock_result
    resp = await mclient.delete(f"/api/cameras/{cam.id}/roi")
    assert resp.status_code == 204


# ── Events ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_events_empty(mclient, mock_db):
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = mock_result
    resp = await mclient.get("/api/events")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_event_not_found(mclient, mock_db):
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result
    resp = await mclient.get(f"/api/events/{uuid.uuid4()}")
    assert resp.status_code == 404


# ── Alerts ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_alerts_empty(mclient, mock_db):
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = mock_result
    resp = await mclient.get("/api/alerts")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_acknowledge_alert_not_found(mclient, mock_db):
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result
    resp = await mclient.patch(f"/api/alerts/{uuid.uuid4()}", json={"status": "acknowledged"})
    assert resp.status_code == 404


# ── Evidence ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_evidence_empty(mclient, mock_db):
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = mock_result
    resp = await mclient.get("/api/evidence")
    assert resp.status_code == 200


# ── LPR ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_plates_empty(mclient, mock_db):
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = mock_result
    resp = await mclient.get("/api/lpr/plates")
    assert resp.status_code == 200


# ── Config ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_config(mclient, mock_db):
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = mock_result
    resp = await mclient.get("/api/config")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_config_not_found(mclient, mock_db):
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result
    resp = await mclient.get("/api/config/nonexistent_key")
    assert resp.status_code == 404


# ── Analytics ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_analytics_summary_via_route(mclient, mock_db):
    mock_db.scalar.return_value = 0
    resp = await mclient.get("/api/analytics/summary")
    assert resp.status_code == 200

@pytest.mark.asyncio
async def test_analytics_hourly_via_route(mclient, mock_db):
    mock_result = MagicMock()
    mock_result.__iter__ = MagicMock(return_value=iter([]))
    mock_db.execute.return_value = mock_result
    resp = await mclient.get("/api/analytics/hourly")
    assert resp.status_code == 200


# ── Logs ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_logs_search_no_query(mclient, mock_db):
    resp = await mclient.get("/api/system/logs")
    assert resp.status_code == 200
    assert resp.json()["results"] == []

@pytest.mark.asyncio
async def test_logs_search_with_query(mclient, mock_db):
    with patch("app.api.routes.logs._qdrant") as mock_qdrant:
        mock_qdrant.search = AsyncMock(return_value=[{"message": "test"}])
        resp = await mclient.get("/api/system/logs?q=error")
    assert resp.status_code == 200

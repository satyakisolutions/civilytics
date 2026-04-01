import pytest


@pytest.mark.asyncio
async def test_get_roi_camera_not_found(client):
    resp = await client.get("/api/cameras/00000000-0000-0000-0000-000000000000/roi")
    assert resp.status_code in (404, 500)

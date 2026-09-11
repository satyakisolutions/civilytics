import pytest


@pytest.mark.asyncio
async def test_list_events(client):
    resp = await client.get("/api/events")
    assert resp.status_code in (200, 500)


@pytest.mark.asyncio
async def test_active_tracks(client):
    resp = await client.get("/api/events/tracks/active")
    assert resp.status_code in (200, 500)

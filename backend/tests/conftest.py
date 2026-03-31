import pytest
import httpx
from httpx import ASGITransport


@pytest.fixture
def app():
    from app.main import create_app
    return create_app()


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as ac:
        yield ac

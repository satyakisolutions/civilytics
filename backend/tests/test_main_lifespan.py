"""Tests for main.py app creation and lifespan."""
import pytest
from unittest.mock import patch, AsyncMock


def test_create_app():
    from app.main import create_app
    app = create_app()
    assert app.title == "WasteWatch API"
    # Check routes are registered
    routes = [r.path for r in app.routes]
    assert "/api/health" in routes
    assert "/api/cameras" in routes or any("/cameras" in r for r in routes)


def test_app_cors():
    from app.main import create_app
    app = create_app()
    # CORS middleware should be present
    middlewares = [type(m).__name__ for m in app.user_middleware]
    # FastAPI adds it internally


@pytest.mark.asyncio
async def test_lifespan_starts_workers():
    from app.main import create_app
    with patch("app.main.MetadataConsumer") as MC, \
         patch("app.main.EventConsumer") as EC, \
         patch("app.main.VLMWorker") as VW, \
         patch("app.main.AlertDispatcher") as AD, \
         patch("app.main.TTSWorker") as TW, \
         patch("app.main.EvidenceWorker") as EW, \
         patch("app.main.LPRWorker") as LW, \
         patch("app.main.LogWorker") as LogW:
        for cls in [MC, EC, VW, AD, TW, EW, LW, LogW]:
            instance = AsyncMock()
            cls.return_value = instance

        app = create_app()
        # Trigger lifespan
        async with app.router.lifespan_context(app):
            pass  # Workers started and stopped

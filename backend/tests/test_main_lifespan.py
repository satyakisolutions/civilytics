"""Tests for main.py app creation and lifespan."""
import pytest
from unittest.mock import patch, AsyncMock


def test_create_app():
    from app.main import create_app
    app = create_app()
    assert app.title == "WasteWatch API"
    # Check routes are registered.
    # app.routes mixes real routes with _IncludedRouter entries (one per
    # include_router call) which carry no .path, so read it defensively.
    routes = [p for p in (getattr(r, "path", None) for r in app.routes) if p]
    # Routers added via include_router are not flattened into app.routes in
    # this FastAPI version (they appear as _IncludedRouter), so assert against
    # the generated OpenAPI paths instead -- that reflects what is actually
    # served.
    paths = app.openapi()["paths"]
    assert "/api/health" in paths
    assert "/api/cameras" in paths
    assert "/api/auth/login" in paths


def test_app_cors():
    from app.main import create_app
    app = create_app()
    # CORS middleware should be present
    middlewares = [type(m).__name__ for m in app.user_middleware]
    # FastAPI adds it internally


@pytest.mark.asyncio
async def test_lifespan_starts_consumers():
    """The lifespan starts the four Kafka consumers that feed the WebSockets.

    This test previously patched app.main.VLMWorker, AlertDispatcher, TTSWorker,
    EvidenceWorker, LPRWorker and LogWorker -- none of which main.py imports any
    more. It failed on the first missing attribute, but that was masked by an
    earlier PermissionError from the hardcoded /app/evidence mount, so the drift
    went unnoticed. Asserted against what lifespan() actually constructs.
    """
    from app.main import create_app
    with patch("app.main.MetadataConsumer") as MC, \
         patch("app.main.EventConsumer") as EC, \
         patch("app.main.AlertConsumer") as AC, \
         patch("app.main.CameraStatusConsumer") as CSC:
        for cls in (MC, EC, AC, CSC):
            cls.return_value = AsyncMock()

        app = create_app()
        async with app.router.lifespan_context(app):
            pass

        for cls in (MC, EC, AC, CSC):
            cls.return_value.start.assert_awaited_once()
            cls.return_value.stop.assert_awaited_once()

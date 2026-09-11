from starlette.testclient import TestClient
from app.main import create_app


def test_ws_metadata_connects():
    """Test that a WebSocket connection to /ws/metadata/{camera_id} is accepted."""
    app = create_app()
    client = TestClient(app)
    with client.websocket_connect("/ws/metadata/cam-01") as ws:
        pass  # Connection accepted and closed cleanly = success

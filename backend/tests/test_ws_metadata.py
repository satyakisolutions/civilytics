"""WebSocket /ws/metadata auth.

This file previously asserted that an unauthenticated connection is
ACCEPTED, which encoded the vulnerability as expected behaviour: any peer
able to reach the port received live per-camera detection metadata. It now
asserts the opposite in production mode, and keeps a dev-mode case so local
bring-up without Keycloak still works.
"""
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import create_app


def test_ws_metadata_rejects_unauthenticated(monkeypatch):
    monkeypatch.delenv("WW_AUTH_DEV_MODE", raising=False)
    client = TestClient(create_app())
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/metadata/cam-01"):
            pass
    assert exc.value.code == 1008, "expected policy-violation close"


def test_ws_metadata_rejects_bad_token(monkeypatch):
    monkeypatch.delenv("WW_AUTH_DEV_MODE", raising=False)
    client = TestClient(create_app())
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(
            "/ws/metadata/cam-01", subprotocols=["bearer", "not-a-jwt"]
        ):
            pass
    assert exc.value.code == 1008


def test_ws_metadata_connects_in_dev_mode(monkeypatch):
    monkeypatch.setenv("WW_AUTH_DEV_MODE", "1")
    client = TestClient(create_app())
    with client.websocket_connect("/ws/metadata/cam-01"):
        pass  # accepted and closed cleanly

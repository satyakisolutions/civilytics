"""WebSocket auth: the three /ws endpoints were previously open to any peer."""
import pytest

from app.auth import ws_auth


class _FakeWS:
    """Minimal WebSocket stand-in recording accept/close calls."""

    def __init__(self, subprotocols=None):
        self.scope = {"subprotocols": subprotocols or []}
        self.accepted = False
        self.accepted_subprotocol = None
        self.closed_code = None

    async def accept(self, subprotocol=None):
        self.accepted = True
        self.accepted_subprotocol = subprotocol

    async def close(self, code=None, reason=None):
        self.closed_code = code


@pytest.fixture(autouse=True)
def _no_dev_mode(monkeypatch):
    """Dev mode would accept everything; these tests assert production behaviour."""
    monkeypatch.delenv("WW_AUTH_DEV_MODE", raising=False)


@pytest.mark.asyncio
async def test_rejects_connection_with_no_token():
    ws = _FakeWS()
    assert await ws_auth.authenticate_ws(ws) is None
    assert ws.accepted is False, "must not accept before auth"
    assert ws.closed_code == 1008


@pytest.mark.asyncio
async def test_rejects_malformed_subprotocol():
    # Token present but not preceded by the "bearer" marker.
    ws = _FakeWS(subprotocols=["sometoken"])
    assert await ws_auth.authenticate_ws(ws) is None
    assert ws.accepted is False


@pytest.mark.asyncio
async def test_rejects_invalid_token(monkeypatch):
    async def _reject(_token):
        return None

    monkeypatch.setattr(ws_auth, "validate_token", _reject)
    ws = _FakeWS(subprotocols=["bearer", "garbage"])
    assert await ws_auth.authenticate_ws(ws) is None
    assert ws.accepted is False
    assert ws.closed_code == 1008


@pytest.mark.asyncio
async def test_rejects_valid_token_without_required_role(monkeypatch):
    async def _ok(_token):
        return {"preferred_username": "bob", "realm_access": {"roles": []}}

    monkeypatch.setattr(ws_auth, "validate_token", _ok)
    ws = _FakeWS(subprotocols=["bearer", "good"])
    assert await ws_auth.authenticate_ws(ws, role="viewer") is None
    assert ws.accepted is False


@pytest.mark.asyncio
async def test_accepts_valid_token_and_echoes_subprotocol(monkeypatch):
    async def _ok(_token):
        return {"preferred_username": "bob", "realm_access": {"roles": ["viewer"]}}

    monkeypatch.setattr(ws_auth, "validate_token", _ok)
    ws = _FakeWS(subprotocols=["bearer", "good"])
    claims = await ws_auth.authenticate_ws(ws, role="viewer")
    assert claims is not None
    assert ws.accepted is True
    # The browser aborts unless the server echoes one of the offered protocols.
    assert ws.accepted_subprotocol == "bearer"


@pytest.mark.asyncio
async def test_dev_mode_accepts_without_token(monkeypatch):
    monkeypatch.setenv("WW_AUTH_DEV_MODE", "1")
    ws = _FakeWS()
    claims = await ws_auth.authenticate_ws(ws)
    assert claims is not None
    assert ws.accepted is True

"""Tests for auth: keycloak.py and middleware.py."""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from jose import JWTError
from starlette.requests import Request
from starlette.responses import Response


# ── keycloak.py ─────────────────────────────────────────────────────

from app.auth.keycloak import extract_roles, has_role


def test_extract_roles():
    claims = {"realm_access": {"roles": ["admin", "viewer"]}}
    assert extract_roles(claims) == ["admin", "viewer"]

def test_extract_roles_missing():
    assert extract_roles({}) == []
    assert extract_roles({"realm_access": {}}) == []

def test_has_role_true():
    claims = {"realm_access": {"roles": ["admin", "viewer"]}}
    assert has_role(claims, "admin") is True

def test_has_role_false():
    claims = {"realm_access": {"roles": ["viewer"]}}
    assert has_role(claims, "admin") is False


@pytest.mark.asyncio
async def test_validate_token_success():
    import app.auth.keycloak as kc
    kc._jwks_cache = None  # Reset cache
    mock_jwks = {"keys": [{"kid": "test"}]}
    mock_claims = {"sub": "user1", "realm_access": {"roles": ["admin"]}}

    with patch("app.auth.keycloak.httpx.AsyncClient") as MockClient:
        mock_resp = AsyncMock()
        mock_resp.json.return_value = mock_jwks
        mock_resp.raise_for_status = MagicMock()
        mc = AsyncMock()
        mc.get.return_value = mock_resp
        mc.__aenter__ = AsyncMock(return_value=mc)
        mc.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mc

        with patch("app.auth.keycloak.jwt.decode", return_value=mock_claims):
            result = await kc.validate_token("valid_token")
    assert result == mock_claims
    kc._jwks_cache = None  # Cleanup


@pytest.mark.asyncio
async def test_validate_token_jwt_error():
    import app.auth.keycloak as kc
    original = kc._jwks_cache
    kc._jwks_cache = {"keys": []}

    try:
        with patch("app.auth.keycloak.jwt.decode", side_effect=JWTError("expired")):
            result = await kc.validate_token("bad_token")
        assert result is None
    finally:
        kc._jwks_cache = original


@pytest.mark.asyncio
async def test_jwks_caching():
    import app.auth.keycloak as kc
    kc._jwks_cache = {"keys": [{"cached": True}]}
    result = await kc.get_jwks()
    assert result == {"keys": [{"cached": True}]}
    kc._jwks_cache = None


# ── middleware.py ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_current_user_dev_mode():
    from app.auth.middleware import get_current_user
    req = MagicMock()
    req.headers.get.return_value = ""
    req.cookies = {}  # no browser session; middleware also checks cookies
    # WW_AUTH_DEV_MODE is set in conftest.py
    user = await get_current_user(req)
    assert user["sub"] == "dev-user"
    assert "admin" in user["realm_access"]["roles"]


@pytest.mark.asyncio
async def test_get_current_user_prod_mode_no_header():
    """In production (no dev mode), missing auth header should return 401."""
    import os
    from fastapi import HTTPException
    from app.auth.middleware import get_current_user
    req = MagicMock()
    req.headers.get.return_value = ""
    req.cookies = {}
    old = os.environ.pop("WW_AUTH_DEV_MODE", None)
    try:
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(req)
        assert exc_info.value.status_code == 401
    finally:
        if old:
            os.environ["WW_AUTH_DEV_MODE"] = old


@pytest.mark.asyncio
async def test_get_current_user_valid_token():
    from app.auth.middleware import get_current_user
    claims = {"sub": "real-user", "realm_access": {"roles": ["viewer"]}}
    req = MagicMock()
    req.headers.get.return_value = "Bearer valid_token"
    req.cookies = {}

    with patch("app.auth.middleware.validate_token", new_callable=AsyncMock, return_value=claims):
        user = await get_current_user(req)
    assert user["sub"] == "real-user"


@pytest.mark.asyncio
async def test_get_current_user_invalid_token():
    from fastapi import HTTPException
    from app.auth.middleware import get_current_user
    req = MagicMock()
    req.headers.get.return_value = "Bearer invalid_token"
    req.cookies = {}

    with patch("app.auth.middleware.validate_token", new_callable=AsyncMock, return_value=None):
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(req)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_require_role_success():
    from app.auth.middleware import require_role
    checker = require_role("admin")
    user = {"sub": "u1", "realm_access": {"roles": ["admin"]}}
    result = await checker(user=user)
    assert result == user


@pytest.mark.asyncio
async def test_require_role_forbidden():
    from fastapi import HTTPException
    from app.auth.middleware import require_role
    checker = require_role("owner")
    user = {"sub": "u1", "realm_access": {"roles": ["viewer"]}}
    with pytest.raises(HTTPException) as exc_info:
        await checker(user=user)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_get_current_user_accepts_cookie_session():
    """Browser session: token arrives in the httpOnly cookie, not a header."""
    from app.auth.middleware import get_current_user
    claims = {"sub": "cookie-user", "realm_access": {"roles": ["viewer"]}}
    req = MagicMock()
    req.headers.get.return_value = ""           # no Authorization header
    req.cookies = {"ww_access": "cookie_token"}

    with patch("app.auth.middleware.validate_token", new_callable=AsyncMock, return_value=claims):
        user = await get_current_user(req)
    assert user["sub"] == "cookie-user"


@pytest.mark.asyncio
async def test_bearer_header_wins_over_cookie():
    """An explicit Authorization header takes precedence over the cookie."""
    from app.auth.middleware import get_current_user
    seen = {}

    async def _capture(token):
        seen["token"] = token
        return {"sub": "hdr", "realm_access": {"roles": ["viewer"]}}

    req = MagicMock()
    req.headers.get.return_value = "Bearer header_token"
    req.cookies = {"ww_access": "cookie_token"}

    with patch("app.auth.middleware.validate_token", new=_capture):
        await get_current_user(req)
    assert seen["token"] == "header_token"


@pytest.mark.asyncio
async def test_logout_returns_the_response_with_cookie_deletions():
    """Logout must not replace the Response after mutating its cookies."""
    from app.api.routes.auth import logout

    request = Request({"type": "http", "headers": []})
    response = Response()

    returned = await logout(request, response)

    assert returned is response
    assert returned.status_code == 204
    set_cookies = returned.headers.getlist("set-cookie")
    assert any("ww_access=" in value and "Max-Age=0" in value for value in set_cookies)
    assert any("ww_refresh=" in value and "Max-Age=0" in value for value in set_cookies)

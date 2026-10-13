# backend/app/api/routes/auth.py
"""Branded login for Civilytics -- server-side Keycloak token exchange.

The product requires a Civilytics-branded login form rather than Keycloak's
own login page, so this uses Keycloak's Direct Access Grant
(grant_type=password).

The exchange happens HERE, server side, not in the browser. That buys three
things over calling Keycloak's token endpoint from React:

  1. `keycloak_client_secret` never reaches the browser, so the client can be
     confidential rather than public.
  2. Tokens live in httpOnly cookies, unreachable from JavaScript. An XSS bug
     in the dashboard cannot exfiltrate a session.
  3. The refresh token is never exposed to the page at all.

KNOWN LIMITATIONS of the Direct Access Grant, accepted deliberately:
  - Interactive MFA (OTP, WebAuthn) cannot work -- there is no second step in
    this grant. Requiring MFA later means moving to the redirect flow with a
    custom Keycloak login theme.
  - Password reset, email verification and the lockout UX are Keycloak login
    FLOW features and are likewise unavailable here.
  - Keycloak must have "Direct access grants" enabled on the client; it is
    off by default on newly created clients in recent versions.
"""
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.auth import kc_admin
from app.auth.keycloak import extract_roles, validate_token
from app.auth.middleware import get_current_user
from app.config.settings import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

ACCESS_COOKIE = "ww_access"
REFRESH_COOKIE = "ww_refresh"

# Keep the access cookie's lifetime short; the refresh cookie carries the
# session. Keycloak's own token lifespans still govern actual validity --
# these are only the browser's retention hints.
_ACCESS_MAX_AGE_S = 15 * 60
_REFRESH_MAX_AGE_S = 8 * 60 * 60


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=512)


class UserResponse(BaseModel):
    username: str
    name: str | None = None
    email: str | None = None
    roles: list[str]


def _token_url() -> str:
    return (
        f"{settings.keycloak_url}/realms/{settings.keycloak_realm}"
        f"/protocol/openid-connect/token"
    )


def _logout_url() -> str:
    return (
        f"{settings.keycloak_url}/realms/{settings.keycloak_realm}"
        f"/protocol/openid-connect/logout"
    )


def _set_cookie(response: Response, name: str, value: str, max_age: int) -> None:
    kwargs = {
        "key": name,
        "value": value,
        "max_age": max_age,
        "httponly": True,
        "secure": settings.auth_cookie_secure,
        "samesite": settings.auth_cookie_samesite,
        "path": "/",
    }
    if settings.auth_cookie_domain:
        kwargs["domain"] = settings.auth_cookie_domain
    response.set_cookie(**kwargs)


def _clear_cookie(response: Response, name: str) -> None:
    kwargs = {"key": name, "path": "/"}
    if settings.auth_cookie_domain:
        kwargs["domain"] = settings.auth_cookie_domain
    response.delete_cookie(**kwargs)


def _user_from_claims(claims: dict) -> UserResponse:
    return UserResponse(
        username=claims.get("preferred_username") or claims.get("sub", ""),
        name=claims.get("name"),
        email=claims.get("email"),
        roles=extract_roles(claims),
    )


async def _exchange(form: dict) -> dict:
    """POST to Keycloak's token endpoint. Raises HTTPException on failure."""
    form = {**form, "client_id": settings.keycloak_client_id}
    if settings.keycloak_client_secret:
        form["client_secret"] = settings.keycloak_client_secret
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(_token_url(), data=form)
    except httpx.RequestError:
        logger.exception("Keycloak unreachable during token exchange")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable",
        )

    if resp.status_code != 200:
        # Log the status but NOT the body -- Keycloak error payloads can echo
        # the submitted username, and this path handles credentials.
        logger.warning("Keycloak token exchange failed: HTTP %d", resp.status_code)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    return resp.json()


@router.post("/login", response_model=UserResponse)
async def login(body: LoginRequest, response: Response):
    """Exchange credentials for a session. Sets httpOnly cookies."""
    tokens = await _exchange(
        {
            "grant_type": "password",
            "username": body.username,
            "password": body.password,
            "scope": "openid",
        }
    )
    access = tokens.get("access_token")
    refresh = tokens.get("refresh_token")
    if not access:
        raise HTTPException(status_code=502, detail="No access token returned")

    claims = await validate_token(access)
    if not claims:
        # Keycloak issued a token we cannot verify -- misconfigured audience or
        # realm. Fail closed rather than trusting an unverified token.
        logger.error("Keycloak issued a token that failed local validation")
        raise HTTPException(status_code=502, detail="Token validation failed")

    _set_cookie(response, ACCESS_COOKIE, access, _ACCESS_MAX_AGE_S)
    if refresh:
        _set_cookie(response, REFRESH_COOKIE, refresh, _REFRESH_MAX_AGE_S)
    logger.info("Login succeeded for %s", claims.get("preferred_username"))
    return _user_from_claims(claims)


@router.post("/refresh", response_model=UserResponse)
async def refresh_session(request: Request, response: Response):
    """Mint a new access token from the refresh cookie."""
    refresh = request.cookies.get(REFRESH_COOKIE)
    if not refresh:
        raise HTTPException(status_code=401, detail="No refresh token")

    tokens = await _exchange({"grant_type": "refresh_token", "refresh_token": refresh})
    access = tokens.get("access_token")
    claims = await validate_token(access) if access else None
    if not claims:
        # Refresh failed or returned something unverifiable -- clear the
        # session so the client stops retrying with a dead cookie.
        _clear_cookie(response, ACCESS_COOKIE)
        _clear_cookie(response, REFRESH_COOKIE)
        raise HTTPException(status_code=401, detail="Session expired")

    _set_cookie(response, ACCESS_COOKIE, access, _ACCESS_MAX_AGE_S)
    if tokens.get("refresh_token"):
        _set_cookie(response, REFRESH_COOKIE, tokens["refresh_token"], _REFRESH_MAX_AGE_S)
    return _user_from_claims(claims)


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response):
    """Clear cookies and revoke the refresh token at Keycloak."""
    refresh = request.cookies.get(REFRESH_COOKIE)
    _clear_cookie(response, ACCESS_COOKIE)
    _clear_cookie(response, REFRESH_COOKIE)
    if refresh:
        form = {"client_id": settings.keycloak_client_id, "refresh_token": refresh}
        if settings.keycloak_client_secret:
            form["client_secret"] = settings.keycloak_client_secret
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(_logout_url(), data=form)
        except httpx.RequestError:
            # Cookies are already cleared, so the browser session is over
            # regardless. Keycloak-side revocation is best effort.
            logger.warning("Keycloak logout call failed; cookies cleared anyway")
    # Return the SAME response the delete-cookie headers were written to.
    # Building a fresh Response(status_code=204) here discards those headers,
    # so the browser keeps both cookies and the session is never cleared.
    response.status_code = 204
    return response


class ForgotPasswordRequest(BaseModel):
    username: str = Field(min_length=1, max_length=255)


@router.post("/forgot-password", status_code=202)
async def forgot_password(body: ForgotPasswordRequest):
    """Trigger a Keycloak reset-password email for the given username.

    Public and unauthenticated, so it must not leak whether an account
    exists: it always returns 202 regardless of lookup result or SMTP
    outcome. Delivery still requires realm SMTP to be configured.
    """
    try:
        user = await kc_admin.find_user_by_username(body.username.strip())
        if user and user.get("email"):
            await kc_admin.send_password_reset_email(user["id"])
    except Exception:
        # Never surface lookup/SMTP errors to an anonymous caller — that
        # would turn this into a user-enumeration / SMTP-probe oracle.
        logger.warning("forgot-password: send failed for a submitted username", exc_info=True)
    return Response(status_code=202)


@router.get("/me", response_model=UserResponse)
async def me(user: dict = Depends(get_current_user)):
    """Who am I. The frontend calls this on load to restore a session."""
    return _user_from_claims(user)


@router.get("/ws-token")
async def ws_token(request: Request, user: dict = Depends(get_current_user)):
    """Hand the page the access token for the WebSocket subprotocol.

    Browsers cannot attach cookies usefully to a cross-origin WebSocket and
    cannot set headers on one at all, so the page needs the raw token to pass
    as a subprotocol (see app/auth/ws_auth.py). This endpoint is the ONLY
    place a token is exposed to JavaScript; it is deliberately not stored.
    """
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        # Bearer-authenticated caller (service/integration) -- it already holds
        # its own token and does not need one minted here.
        raise HTTPException(status_code=400, detail="No cookie session")
    return {"token": token}

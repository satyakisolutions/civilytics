# backend/app/auth/ws_auth.py
"""WebSocket authentication.

Browsers cannot set request headers on `new WebSocket(...)`, so the usual
`Authorization: Bearer` route is unavailable. The two options are a query
parameter or the WebSocket subprotocol; we use the subprotocol because
query strings land in reverse-proxy and application access logs, which
would persist a usable bearer token to disk.

Client side:

    new WebSocket(url, ["bearer", accessToken])

The token arrives in `Sec-WebSocket-Protocol`. We validate it BEFORE
calling accept(), so an unauthenticated peer never joins a subscriber
set and never receives a frame. On success we must echo exactly one of
the offered subprotocols back ("bearer"), or the browser aborts the
connection.

SECURITY: these endpoints were previously unauthenticated -- any peer
that could reach the port received the live alert stream, the event
stream and per-camera detection metadata, bypassing the REST RBAC
entirely.
"""
import logging
import os

from fastapi import WebSocket, status

from app.auth.keycloak import validate_token, has_role

logger = logging.getLogger(__name__)

_SUBPROTOCOL = "bearer"


def _is_dev_mode() -> bool:
    """Mirrors app.auth.middleware._is_dev_mode -- opt-in only, never default."""
    return os.environ.get("WW_AUTH_DEV_MODE", "").lower() in ("1", "true", "yes")


def _extract_token(websocket: WebSocket) -> str | None:
    """Pull the token from the offered subprotocols.

    Starlette exposes the parsed `Sec-WebSocket-Protocol` list on the scope.
    We expect ["bearer", "<token>"]; anything else is treated as absent.
    """
    offered = websocket.scope.get("subprotocols") or []
    if len(offered) >= 2 and offered[0] == _SUBPROTOCOL:
        return offered[1] or None
    return None


async def authenticate_ws(websocket: WebSocket, role: str = "viewer") -> dict | None:
    """Validate the connection and accept it, or close it and return None.

    Returns the token claims on success. Callers must treat a None return as
    "connection already closed" and do nothing further.
    """
    token = _extract_token(websocket)

    if token is None:
        if _is_dev_mode():
            logger.debug("Dev mode: accepting unauthenticated WebSocket")
            await websocket.accept()
            return {
                "sub": "dev-user",
                "preferred_username": "dev",
                "realm_access": {"roles": ["owner", "admin", "viewer"]},
            }
        logger.warning("WebSocket rejected: no bearer subprotocol")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Unauthorized")
        return None

    claims = await validate_token(token)
    if not claims:
        logger.warning("WebSocket rejected: invalid or expired token")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Unauthorized")
        return None

    if not has_role(claims, role):
        logger.warning(
            "WebSocket rejected: user %s lacks role %s",
            claims.get("preferred_username"), role,
        )
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Forbidden")
        return None

    # Echo the subprotocol back, or the browser drops the connection.
    await websocket.accept(subprotocol=_SUBPROTOCOL)
    return claims

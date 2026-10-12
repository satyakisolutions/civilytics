# backend/app/auth/middleware.py
"""FastAPI auth dependencies for role-based access control."""
import hmac
import logging
import os

from fastapi import Depends, HTTPException, Request

from app.auth.keycloak import validate_token, extract_roles, has_role

logger = logging.getLogger(__name__)

def _is_dev_mode() -> bool:
    """SECURITY: Dev mode ONLY when explicitly enabled. Never grant access by default."""
    return os.environ.get("WW_AUTH_DEV_MODE", "").lower() in ("1", "true", "yes")


def _service_token() -> str:
    """Shared secret for internal worker-to-api calls (Conductor task workers).

    Unset/empty means the service-token path is disabled entirely — it is
    NOT a dev-mode fallback, so it stays usable with WW_AUTH_DEV_MODE=0.
    """
    return os.environ.get("WW_INTERNAL_SERVICE_TOKEN", "")


def _is_valid_service_token(presented: str) -> bool:
    expected = _service_token()
    if not expected or not presented:
        return False
    return hmac.compare_digest(presented, expected)


async def get_current_user(request: Request) -> dict:
    """Extract and validate the caller's identity. Returns a claims dict.

    Three credential paths, checked in order:
      1. X-Service-Token: shared secret for internal workers (Conductor
         task workers calling e.g. /lpr/analyze, /reports/incident-narrative).
         Independent of dev mode — works with WW_AUTH_DEV_MODE=0.
      2. Authorization: Bearer <jwt>: user or integration token, validated
         against Keycloak.
      3. ww_access cookie: browser session from the branded login form.

    In development mode (WW_AUTH_DEV_MODE=true) only, returns a mock admin
    user when none of the above are present.
    """
    service_token = request.headers.get("X-Service-Token", "")
    if _is_valid_service_token(service_token):
        return {
            "sub": "internal-service",
            "preferred_username": "internal-service",
            "realm_access": {"roles": ["admin", "viewer"]},
        }

    auth_header = request.headers.get("Authorization", "")
    token = ""

    if auth_header.startswith("Bearer "):
        # User or integration bearer token, validated against Keycloak below.
        token = auth_header[7:]
    else:
        # Browser session from the branded login form. The token lives in an
        # httpOnly cookie so JavaScript cannot read it (see
        # api/routes/auth.py). Cookie is checked only when no Bearer header
        # was supplied, so an explicit header always wins.
        token = request.cookies.get("ww_access", "")

    if not token:
        if _is_dev_mode():
            logger.debug("Dev mode: returning mock user (no credentials)")
            return {
                "sub": "dev-user",
                "preferred_username": "dev",
                "realm_access": {"roles": ["owner", "admin", "viewer"]},
            }
        raise HTTPException(status_code=401, detail="Not authenticated")
    claims = await validate_token(token)
    if not claims:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return claims


def require_role(role: str):
    """Dependency that requires a specific role."""
    async def _check(user: dict = Depends(get_current_user)):
        if not has_role(user, role):
            raise HTTPException(status_code=403, detail=f"Role '{role}' required")
        return user
    return _check


require_owner = require_role("owner")
require_admin = require_role("admin")
require_viewer = require_role("viewer")

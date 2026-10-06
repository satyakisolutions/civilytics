# backend/app/auth/middleware.py
"""FastAPI auth dependencies for role-based access control."""
import logging
import os

from fastapi import Depends, HTTPException, Request

from app.auth.keycloak import validate_token, extract_roles, has_role

logger = logging.getLogger(__name__)

def _is_dev_mode() -> bool:
    """SECURITY: Dev mode ONLY when explicitly enabled. Never grant access by default."""
    return os.environ.get("WW_AUTH_DEV_MODE", "").lower() in ("1", "true", "yes")


async def get_current_user(request: Request) -> dict:
    """Extract and validate JWT from Authorization header. Returns claims dict.

    In development mode (WW_AUTH_DEV_MODE=true), returns a mock admin user
    when no Authorization header is present. In production, always requires
    a valid Bearer token.
    """
    auth_header = request.headers.get("Authorization", "")
    token = ""

    if auth_header.startswith("Bearer "):
        # Service-to-service / integration callers.
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

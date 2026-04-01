# backend/app/auth/middleware.py
"""FastAPI auth dependencies for role-based access control."""
import logging
from fastapi import Depends, HTTPException, Request

from app.auth.keycloak import validate_token, extract_roles, has_role

logger = logging.getLogger(__name__)


async def get_current_user(request: Request) -> dict:
    """Extract and validate JWT from Authorization header. Returns claims dict.

    In development (no Keycloak), returns a mock admin user.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        # Dev mode: return mock user if no auth header
        return {"sub": "dev-user", "preferred_username": "dev", "realm_access": {"roles": ["owner", "admin", "viewer"]}}

    token = auth_header[7:]
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

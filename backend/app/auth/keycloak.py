# backend/app/auth/keycloak.py
"""Keycloak JWT token validation and role extraction."""
import logging
from functools import lru_cache

import httpx
from jose import jwt, JWTError

from app.config.settings import settings

logger = logging.getLogger(__name__)

_jwks_cache: dict | None = None


async def get_jwks() -> dict:
    global _jwks_cache
    if _jwks_cache:
        return _jwks_cache
    url = f"{settings.keycloak_url}/realms/{settings.keycloak_realm}/protocol/openid-connect/certs"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        _jwks_cache = resp.json()
        return _jwks_cache


async def validate_token(token: str) -> dict | None:
    """Validate a JWT token against Keycloak JWKS. Returns decoded claims or None."""
    try:
        jwks = await get_jwks()
        claims = jwt.decode(
            token,
            jwks,
            algorithms=["RS256"],
            audience=settings.keycloak_client_id,
            options={"verify_exp": True, "verify_aud": True},
        )
        return claims
    except JWTError as e:
        logger.warning("JWT validation failed: %s", e)
        return None
    except Exception:
        logger.exception("Token validation error")
        return None


def extract_roles(claims: dict) -> list[str]:
    """Extract realm roles from Keycloak token claims."""
    realm_access = claims.get("realm_access", {})
    return realm_access.get("roles", [])


def has_role(claims: dict, role: str) -> bool:
    return role in extract_roles(claims)

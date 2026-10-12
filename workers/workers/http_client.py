"""Shared httpx helpers for worker tasks calling the api container.

Every admin/viewer-gated api route needs the internal service-token header
once WW_AUTH_DEV_MODE=0 (see backend/app/auth/middleware.py). Use
service_headers()/service_client() instead of bare httpx so new tasks don't
reintroduce the same unauthenticated-call gap.
"""
from __future__ import annotations

import httpx

from workers.config import settings


def service_headers() -> dict[str, str]:
    headers = {}
    if settings.internal_service_token:
        headers["X-Service-Token"] = settings.internal_service_token
    return headers


def service_client(**kwargs) -> httpx.AsyncClient:
    headers = {**service_headers(), **kwargs.pop("headers", {})}
    return httpx.AsyncClient(headers=headers, **kwargs)

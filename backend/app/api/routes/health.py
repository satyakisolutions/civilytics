# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""Health API routes — quick status + detailed per-component health.

The simple ``GET /health`` endpoint returns OK/degraded/critical
for load-balancer probes. The ``/health/full`` family of endpoints
reads cached status written by the health-monitor microservice into
Redis keys under ``ww:health:*``.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends

from app.auth.middleware import require_admin
from app.config.settings import settings
from app.schemas.health import HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["system"])

# SECURITY: only the bare `GET /health` liveness probe is public -- load
# balancers and container orchestrators need it unauthenticated, and it
# returns nothing but ok/degraded/critical.
#
# The /health/* detail endpoints were ALSO public, which leaked the camera
# inventory, the IP-speaker/device topology and host CPU/RAM/GPU/disk
# figures to anyone who could reach the port. Note the inconsistency that
# made this obvious: the same information under /api/system/health/*
# already required `admin`. These now match.

REDIS_HEALTH_PREFIX = "ww:health"


async def _get_redis():
    """Lazy-init an async Redis client."""
    import redis.asyncio as aioredis
    return aioredis.from_url(settings.redis_url, decode_responses=True)


async def _read_health_key(key_suffix: str) -> dict[str, Any] | list | None:
    """Read a single health key from Redis, return parsed JSON or None."""
    try:
        r = await _get_redis()
        raw = await r.get(f"{REDIS_HEALTH_PREFIX}:{key_suffix}")
        await r.aclose()
        if raw:
            return json.loads(raw)
    except Exception:
        logger.debug("Failed to read health key: %s", key_suffix, exc_info=True)
    return None


async def _read_all_health() -> dict[str, Any]:
    """Read all ww:health:* keys from Redis and assemble a full status dict."""
    result: dict[str, Any] = {}
    try:
        r = await _get_redis()
        keys = []
        async for key in r.scan_iter(match=f"{REDIS_HEALTH_PREFIX}:*"):
            keys.append(key)
        if keys:
            values = await r.mget(keys)
            for key, val in zip(keys, values):
                component = key.removeprefix(f"{REDIS_HEALTH_PREFIX}:")
                if val:
                    try:
                        result[component] = json.loads(val)
                    except json.JSONDecodeError:
                        result[component] = val
        await r.aclose()
    except Exception:
        logger.debug("Failed to read health keys from Redis", exc_info=True)
    return result


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Simple health probe — returns OK, degraded, or critical.

    DELIBERATELY UNAUTHENTICATED. Docker healthchecks, the nginx /healthz
    path and any load balancer need this without credentials. It exposes
    only an aggregate status string and no component detail.
    """
    overall = await _read_health_key("overall")
    if overall and isinstance(overall, dict):
        status = overall.get("status", "ok")
    else:
        status = "ok"

    return HealthResponse(
        status=status,
        version="0.1.0",
        services={"api": "running"},
    )


@router.get("/health/full")
async def full_health(_user: dict = Depends(require_admin)) -> dict[str, Any]:
    """Full system health — reads cached status from health monitor.

    Returns the aggregated health status of all monitored components
    including input/output streams, models, system resources, and services.
    """
    status = await _read_all_health()
    if not status:
        return {
            "status": "unknown",
            "message": "Health monitor data not available — service may not be running",
        }
    return status


@router.get("/health/cameras")
async def camera_health(_user: dict = Depends(require_admin)) -> dict[str, Any]:
    """Per-camera input and output stream health."""
    input_streams = await _read_health_key("input_streams")
    output_streams = await _read_health_key("output_streams")
    return {
        "input_streams": input_streams or [],
        "output_streams": output_streams or [],
    }


@router.get("/health/speakers")
async def speaker_health(_user: dict = Depends(require_admin)) -> dict[str, Any]:
    """IP speaker connection status."""
    speakers = await _read_health_key("ip_speakers")
    return {"speakers": speakers or []}


@router.get("/health/models")
async def model_health(_user: dict = Depends(require_admin)) -> dict[str, Any]:
    """VLM, TTS, and LPR model status."""
    vlm = await _read_health_key("vlm")
    tts = await _read_health_key("tts")
    lpr = await _read_health_key("lpr")
    return {
        "vlm": vlm or {"status": "unknown"},
        "tts": tts or {"status": "unknown"},
        "lpr": lpr or {"status": "unknown"},
    }


@router.get("/health/system")
async def system_health(_user: dict = Depends(require_admin)) -> dict[str, Any]:
    """CPU, RAM, GPU, and disk status."""
    system = await _read_health_key("system")
    return system or {"status": "unknown"}


@router.get("/health/services")
async def services_health(_user: dict = Depends(require_admin)) -> dict[str, Any]:
    """Kafka, Redis, PostgreSQL, Qdrant, and pipeline status."""
    kafka = await _read_health_key("kafka")
    redis_status = await _read_health_key("redis")
    postgres = await _read_health_key("postgres")
    qdrant = await _read_health_key("qdrant")
    pipeline = await _read_health_key("pipeline")
    return {
        "kafka": kafka or {"status": "unknown"},
        "redis": redis_status or {"status": "unknown"},
        "postgres": postgres or {"status": "unknown"},
        "qdrant": qdrant or {"status": "unknown"},
        "pipeline": pipeline or {"status": "unknown"},
    }

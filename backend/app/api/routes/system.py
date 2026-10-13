# backend/app/api/routes/system.py
"""System monitoring endpoints: GPU, containers, Kafka lag, model health, storage."""
import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth.middleware import require_admin
from app.config.logging import get_levels, set_level
from app.monitoring.gpu import get_gpu_stats
from app.monitoring.containers import get_container_stats
from app.monitoring.kafka_lag import get_consumer_lag
from app.monitoring.model_health import model_health
from app.monitoring.storage import get_storage_stats

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system", tags=["system"])


class LogLevelUpdate(BaseModel):
    level: str = Field(description="CRITICAL|ERROR|WARNING|INFO|DEBUG")
    logger: str | None = Field(
        default=None,
        description="Logger name to scope the change to (e.g. 'app.api'); "
        "omit or 'root' for the root logger.",
    )


@router.get("/log-level")
async def read_log_level(_user: dict = Depends(require_admin)):
    """Current effective log levels (root + tracked app loggers)."""
    return get_levels()


@router.put("/log-level")
async def update_log_level(body: LogLevelUpdate, user: dict = Depends(require_admin)):
    """Change a logger's level at runtime (no restart). Not persisted."""
    try:
        applied = set_level(body.level, body.logger)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    logger.warning(
        "log level set to %s for logger %r by %s",
        applied, body.logger or "root", user.get("preferred_username"),
    )
    return {"applied": applied, "logger": body.logger or "root", "levels": get_levels()}


@router.get("/health/gpu")
async def gpu_health(_user: dict = Depends(require_admin)):
    """GPU utilization, memory, and temperature via nvidia-smi."""
    return {"gpus": await get_gpu_stats()}


@router.get("/health/containers")
async def container_health(_user: dict = Depends(require_admin)):
    """Docker container stats."""
    return {"containers": await get_container_stats()}


@router.get("/health/kafka")
async def kafka_health(_user: dict = Depends(require_admin)):
    """Kafka consumer group lag."""
    return {"lag": await get_consumer_lag()}


@router.get("/health/models")
async def models_health(_user: dict = Depends(require_admin)):
    """VLM/model service health."""
    return {"models": model_health.get_all()}


@router.get("/health/storage")
async def storage_health(_user: dict = Depends(require_admin)):
    """Disk/storage usage stats."""
    return {"storage": await get_storage_stats()}


@router.get("/health")
async def system_health(_user: dict = Depends(require_admin)):
    """Aggregated system health summary."""
    gpus = await get_gpu_stats()
    storage = await get_storage_stats()
    return {
        "gpus": gpus,
        "models": model_health.get_all(),
        "storage": storage,
    }

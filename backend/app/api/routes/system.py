# backend/app/api/routes/system.py
"""System monitoring endpoints: GPU, containers, Kafka lag, model health, storage."""
import logging
from fastapi import APIRouter

from app.monitoring.gpu import get_gpu_stats
from app.monitoring.containers import get_container_stats
from app.monitoring.kafka_lag import get_consumer_lag
from app.monitoring.model_health import model_health
from app.monitoring.storage import get_storage_stats

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health/gpu")
async def gpu_health():
    """GPU utilization, memory, and temperature via nvidia-smi."""
    return {"gpus": await get_gpu_stats()}


@router.get("/health/containers")
async def container_health():
    """Docker container stats."""
    return {"containers": await get_container_stats()}


@router.get("/health/kafka")
async def kafka_health():
    """Kafka consumer group lag."""
    return {"lag": await get_consumer_lag()}


@router.get("/health/models")
async def models_health():
    """VLM/model service health."""
    return {"models": model_health.get_all()}


@router.get("/health/storage")
async def storage_health():
    """Disk/storage usage stats."""
    return {"storage": await get_storage_stats()}


@router.get("/health")
async def system_health():
    """Aggregated system health summary."""
    gpus = await get_gpu_stats()
    storage = await get_storage_stats()
    return {
        "gpus": gpus,
        "models": model_health.get_all(),
        "storage": storage,
    }

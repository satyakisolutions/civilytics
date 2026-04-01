# backend/app/api/routes/system.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.monitoring.gpu import get_gpu_stats
from app.monitoring.storage import get_storage_stats
from app.monitoring.containers import get_container_stats
from app.monitoring.kafka_lag import get_consumer_lag
from app.monitoring.model_health import model_health
from app.monitoring.degrade import degrade_manager

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health")
async def system_health():
    return {
        "status": "ok",
        "degrade_level": degrade_manager.level,
    }

@router.get("/gpu")
async def system_gpu():
    return await get_gpu_stats()

@router.get("/storage")
async def system_storage():
    return await get_storage_stats()

@router.get("/containers")
async def system_containers():
    return await get_container_stats()

@router.get("/kafka")
async def system_kafka():
    return await get_consumer_lag()

@router.get("/models")
async def system_models():
    return model_health.get_all()

class DegradeRequest(BaseModel):
    level: int
    reason: str = "manual"

@router.post("/degrade")
async def system_degrade(body: DegradeRequest):
    try:
        degrade_manager.set_level(body.level, body.reason)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return degrade_manager.get_status()

@router.get("/degrade")
async def system_degrade_status():
    return degrade_manager.get_status()

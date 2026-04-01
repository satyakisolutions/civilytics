from fastapi import APIRouter
from app.schemas.health import HealthResponse

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version="0.1.0",
        services={"api": "running", "kafka": "unknown", "database": "unknown"},
    )

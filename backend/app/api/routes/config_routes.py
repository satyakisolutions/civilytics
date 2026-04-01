from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db
from app.db.models import SystemConfig

router = APIRouter(prefix="/config", tags=["config"])

class ConfigValue(BaseModel):
    value: dict | str | int | float | list

class ConfigResponse(BaseModel):
    key: str
    value: dict | str | int | float | list
    updated_at: str | None = None
    model_config = {"from_attributes": True}

@router.get("/{key}", response_model=ConfigResponse)
async def get_config(key: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SystemConfig).where(SystemConfig.key == key))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail=f"Config key '{key}' not found")
    return ConfigResponse(
        key=config.key,
        value=config.value,
        updated_at=config.updated_at.isoformat() if config.updated_at else None,
    )

@router.get("", response_model=list[ConfigResponse])
async def list_config(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SystemConfig).order_by(SystemConfig.key))
    configs = result.scalars().all()
    return [
        ConfigResponse(
            key=c.key,
            value=c.value,
            updated_at=c.updated_at.isoformat() if c.updated_at else None,
        )
        for c in configs
    ]

@router.put("/{key}", response_model=ConfigResponse)
async def set_config(key: str, body: ConfigValue, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SystemConfig).where(SystemConfig.key == key))
    config = result.scalar_one_or_none()
    if config:
        config.value = body.value
    else:
        config = SystemConfig(key=key, value=body.value)
        db.add(config)
    await db.commit()
    await db.refresh(config)
    return ConfigResponse(
        key=config.key,
        value=config.value,
        updated_at=config.updated_at.isoformat() if config.updated_at else None,
    )

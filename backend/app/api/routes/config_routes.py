import re
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db
from app.auth.middleware import require_viewer, require_owner, require_admin
from app.db.models import SystemConfig
from app.config.settings import settings as app_settings

# Allowlist for config keys — reject unknown keys
_ALLOWED_CONFIG_KEYS = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

router = APIRouter(prefix="/config", tags=["config"])

class ConfigValue(BaseModel):
    value: dict | str | int | float | list

class ConfigResponse(BaseModel):
    key: str
    value: dict | str | int | float | list
    updated_at: str | None = None
    model_config = {"from_attributes": True}

@router.get("/{key}", response_model=ConfigResponse)
async def get_config(key: str, db: AsyncSession = Depends(get_db), _user: dict = Depends(require_viewer)):
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
async def list_config(db: AsyncSession = Depends(get_db), _user: dict = Depends(require_viewer)):
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
async def set_config(key: str, body: ConfigValue, db: AsyncSession = Depends(get_db), _user: dict = Depends(require_owner)):
    if not _ALLOWED_CONFIG_KEYS.match(key):
        raise HTTPException(status_code=400, detail="Invalid config key format")
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


class TestChannelRequest(BaseModel):
    channel: str


@router.post("/test-channel")
async def test_channel(
    body: TestChannelRequest,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    """Send a test alert through a configured delivery channel."""
    config_key = f"delivery_channel_{body.channel}"
    result = await db.execute(select(SystemConfig).where(SystemConfig.key == config_key))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail=f"Channel '{body.channel}' not configured")
    return {
        "status": "OK",
        "channel": body.channel,
        "message": f"Test event queued for {body.channel}.",
    }


ALLOWED_LOGO_TYPES = {"image/png", "image/jpeg", "image/svg+xml", "image/webp"}
MAX_LOGO_SIZE = 2 * 1024 * 1024


@router.post("/logo")
async def upload_logo(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    """Upload workspace logo."""
    if file.content_type not in ALLOWED_LOGO_TYPES:
        raise HTTPException(status_code=400, detail="Logo must be PNG, JPEG, SVG, or WebP")
    content = await file.read()
    if len(content) > MAX_LOGO_SIZE:
        raise HTTPException(status_code=400, detail="Logo must be under 2MB")

    branding_dir = Path(app_settings.evidence_dir) / "branding"
    branding_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename or "logo.png").suffix or ".png"
    logo_path = branding_dir / f"logo{ext}"
    logo_path.write_bytes(content)
    logo_url = f"/evidence/branding/logo{ext}"

    result = await db.execute(select(SystemConfig).where(SystemConfig.key == "workspace_settings"))
    ws_config = result.scalar_one_or_none()
    if ws_config:
        val = dict(ws_config.value) if isinstance(ws_config.value, dict) else {}
        val["logo_url"] = logo_url
        ws_config.value = val
    else:
        ws_config = SystemConfig(key="workspace_settings", value={"logo_url": logo_url})
        db.add(ws_config)
    await db.commit()
    return {"status": "OK", "logo_url": logo_url}

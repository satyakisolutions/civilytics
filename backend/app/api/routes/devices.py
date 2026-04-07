# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""Device management API — cameras, IP speakers, escalation contacts.

All device configuration is stored in the SystemConfig table and pushed
to the pipeline via Redis commands when changed.

Config keys used:
    ip_speakers         — list of IP speaker configurations
    escalation_contacts — list of SIP/phone escalation contacts
    zone_operators      — operator → zone assignment

Cameras are managed via the existing /api/cameras CRUD (db-backed).
When a camera is created/updated/deleted, a Redis command notifies the
pipeline to add/remove/reconfigure the RTSP source.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.auth.middleware import require_admin, require_viewer
from app.db.models import SystemConfig

router = APIRouter(prefix="/devices", tags=["devices"])


# ---------------------------------------------------------------------------
# IP Speaker schemas
# ---------------------------------------------------------------------------


class IPSpeakerCreate(BaseModel):
    speaker_id: str = Field(..., pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    name: str
    zone: str
    protocol: str = Field(default="http", pattern=r"^(http|sip|multicast)$")
    endpoint: str               # URL or SIP URI or multicast address
    http_auth_token: str = ""
    volume: int = Field(default=80, ge=0, le=100)
    enabled: bool = True
    camera_ids: list[str] = []  # Which cameras this speaker serves


class IPSpeakerResponse(IPSpeakerCreate):
    pass


# ---------------------------------------------------------------------------
# Escalation contact schemas
# ---------------------------------------------------------------------------


class EscalationContactCreate(BaseModel):
    name: str
    role: str = Field(default="operator", pattern=r"^(operator|supervisor|admin)$")
    zone: str
    phone_number: str = ""      # SIP URI or phone number for voice calls
    telegram_chat_id: str = ""  # Telegram chat ID for push notifications
    delay_s: int = Field(default=60, ge=0, le=600)  # Delay before calling this contact
    level: int = Field(default=1, ge=1, le=5)        # Escalation level (1=first)
    enabled: bool = True


class EscalationContactResponse(EscalationContactCreate):
    pass


# ---------------------------------------------------------------------------
# Zone operator schemas
# ---------------------------------------------------------------------------


class ZoneOperatorCreate(BaseModel):
    operator_id: str
    name: str
    zone: str
    role: str = "operator"
    telegram_chat_id: str = ""
    sip_number: str = ""


class ZoneOperatorResponse(ZoneOperatorCreate):
    pass


# ---------------------------------------------------------------------------
# IP Speaker endpoints
# ---------------------------------------------------------------------------


@router.get("/speakers", response_model=list[IPSpeakerResponse])
async def list_speakers(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_viewer),
):
    """List all configured IP speakers."""
    config = await _get_config(db, "ip_speakers")
    return config or []


@router.put("/speakers", response_model=list[IPSpeakerResponse])
async def set_speakers(
    speakers: list[IPSpeakerCreate],
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    """Replace the full IP speaker configuration."""
    value = [s.model_dump() for s in speakers]
    await _set_config(db, "ip_speakers", value)
    return value


@router.post("/speakers", response_model=IPSpeakerResponse, status_code=201)
async def add_speaker(
    speaker: IPSpeakerCreate,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    """Add a single IP speaker to the configuration."""
    speakers = await _get_config(db, "ip_speakers") or []
    # Check for duplicate speaker_id
    if any(s["speaker_id"] == speaker.speaker_id for s in speakers):
        raise HTTPException(400, f"Speaker '{speaker.speaker_id}' already exists")
    speakers.append(speaker.model_dump())
    await _set_config(db, "ip_speakers", speakers)
    return speaker


@router.delete("/speakers/{speaker_id}", status_code=204)
async def remove_speaker(
    speaker_id: str,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    """Remove an IP speaker by ID."""
    speakers = await _get_config(db, "ip_speakers") or []
    filtered = [s for s in speakers if s["speaker_id"] != speaker_id]
    if len(filtered) == len(speakers):
        raise HTTPException(404, f"Speaker '{speaker_id}' not found")
    await _set_config(db, "ip_speakers", filtered)


# ---------------------------------------------------------------------------
# Escalation contact endpoints
# ---------------------------------------------------------------------------


@router.get("/contacts", response_model=list[EscalationContactResponse])
async def list_contacts(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_viewer),
):
    """List all escalation contacts (ordered by level)."""
    contacts = await _get_config(db, "escalation_contacts") or []
    return sorted(contacts, key=lambda c: (c.get("level", 1), c.get("delay_s", 60)))


@router.put("/contacts", response_model=list[EscalationContactResponse])
async def set_contacts(
    contacts: list[EscalationContactCreate],
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    """Replace the full escalation contact list."""
    value = [c.model_dump() for c in contacts]
    await _set_config(db, "escalation_contacts", value)
    return value


@router.post("/contacts", response_model=EscalationContactResponse, status_code=201)
async def add_contact(
    contact: EscalationContactCreate,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    """Add a single escalation contact."""
    contacts = await _get_config(db, "escalation_contacts") or []
    contacts.append(contact.model_dump())
    await _set_config(db, "escalation_contacts", contacts)
    return contact


@router.delete("/contacts/{name}", status_code=204)
async def remove_contact(
    name: str,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    """Remove an escalation contact by name."""
    contacts = await _get_config(db, "escalation_contacts") or []
    filtered = [c for c in contacts if c["name"] != name]
    if len(filtered) == len(contacts):
        raise HTTPException(404, f"Contact '{name}' not found")
    await _set_config(db, "escalation_contacts", filtered)


# ---------------------------------------------------------------------------
# Zone operator endpoints
# ---------------------------------------------------------------------------


@router.get("/operators", response_model=list[ZoneOperatorResponse])
async def list_operators(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_viewer),
):
    """List all zone operators."""
    return await _get_config(db, "zone_operators") or []


@router.put("/operators", response_model=list[ZoneOperatorResponse])
async def set_operators(
    operators: list[ZoneOperatorCreate],
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    """Replace the full zone operator list."""
    value = [o.model_dump() for o in operators]
    await _set_config(db, "zone_operators", value)
    return value


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_config(db: AsyncSession, key: str) -> list | None:
    result = await db.execute(select(SystemConfig).where(SystemConfig.key == key))
    config = result.scalar_one_or_none()
    if config and isinstance(config.value, list):
        return config.value
    if config and isinstance(config.value, dict) and "items" in config.value:
        return config.value["items"]
    return None


async def _set_config(db: AsyncSession, key: str, value: list) -> None:
    result = await db.execute(select(SystemConfig).where(SystemConfig.key == key))
    config = result.scalar_one_or_none()
    if config:
        config.value = value
    else:
        config = SystemConfig(key=key, value=value)
        db.add(config)
    await db.commit()

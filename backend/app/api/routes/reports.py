# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""Report configuration and manual trigger API.

Endpoints for managing email report recipients, manually triggering
report generation, and viewing report history.

Config key: ``report_recipients`` in SystemConfig.
"""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.auth.middleware import require_admin, require_viewer
from app.db.models import SystemConfig

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/reports", tags=["reports"])

IST = ZoneInfo("Asia/Kolkata")

CONFIG_KEY_RECIPIENTS = "report_recipients"
CONFIG_KEY_HISTORY = "report_history"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ReportRecipientCreate(BaseModel):
    email: EmailStr
    name: str = Field(..., min_length=1, max_length=255)
    report_types: list[str] = Field(
        default=["weekly", "monthly"],
        description="Which reports to receive: weekly, monthly, or both.",
    )
    enabled: bool = True


class ReportRecipientResponse(ReportRecipientCreate):
    pass


class ReportHistoryEntry(BaseModel):
    type: str
    period_start: str
    period_end: str
    total_events: int
    generated_at: str
    recipients_count: int


class ManualReportResponse(BaseModel):
    status: str
    message: str
    report_type: str


# ---------------------------------------------------------------------------
# Recipients CRUD
# ---------------------------------------------------------------------------


@router.get("/recipients", response_model=list[ReportRecipientResponse])
async def list_recipients(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_viewer),
):
    """List all configured report email recipients."""
    recipients = await _get_recipients(db)
    return recipients


@router.put("/recipients", response_model=list[ReportRecipientResponse])
async def set_recipients(
    recipients: list[ReportRecipientCreate],
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    """Replace the full list of report recipients."""
    value = [r.model_dump() for r in recipients]
    await _set_config(db, CONFIG_KEY_RECIPIENTS, value)
    return value


@router.post(
    "/recipients", response_model=ReportRecipientResponse, status_code=201
)
async def add_recipient(
    recipient: ReportRecipientCreate,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    """Add a single report recipient."""
    recipients = await _get_recipients(db)
    # Prevent duplicate emails
    if any(r["email"] == recipient.email for r in recipients):
        raise HTTPException(
            400, f"Recipient '{recipient.email}' already exists"
        )
    recipients.append(recipient.model_dump())
    await _set_config(db, CONFIG_KEY_RECIPIENTS, recipients)
    return recipient


@router.delete("/recipients/{email}", status_code=204)
async def remove_recipient(
    email: str,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    """Remove a report recipient by email address."""
    recipients = await _get_recipients(db)
    filtered = [r for r in recipients if r["email"] != email]
    if len(filtered) == len(recipients):
        raise HTTPException(404, f"Recipient '{email}' not found")
    await _set_config(db, CONFIG_KEY_RECIPIENTS, filtered)


# ---------------------------------------------------------------------------
# Manual trigger
# ---------------------------------------------------------------------------


@router.post(
    "/generate/{report_type}", response_model=ManualReportResponse
)
async def generate_report(
    report_type: str,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    """Manually trigger report generation (admin only).

    Generates the report inline and sends it via email.
    Valid report_type values: ``weekly``, ``monthly``.
    """
    if report_type not in ("weekly", "monthly"):
        raise HTTPException(
            400,
            f"Invalid report_type '{report_type}'. Must be 'weekly' or 'monthly'.",
        )

    # Lazy import to avoid circular deps — the scheduler is a standalone
    # service but we can reuse its generation logic from the API too.
    from services.reports.main import ReportScheduler

    scheduler = ReportScheduler()
    try:
        if report_type == "weekly":
            report = await scheduler.generate_weekly_report()
        else:
            report = await scheduler.generate_monthly_report()

        await scheduler.send_report(report, report_type)
        await scheduler._save_report_history(report, report_type)
    finally:
        await scheduler._engine.dispose()

    return ManualReportResponse(
        status="ok",
        message=f"{report_type.title()} report generated and sent.",
        report_type=report_type,
    )


# ---------------------------------------------------------------------------
# Report history
# ---------------------------------------------------------------------------


@router.get("/history", response_model=list[ReportHistoryEntry])
async def list_report_history(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_viewer),
):
    """List previously generated reports with timestamps."""
    result = await db.execute(
        select(SystemConfig).where(SystemConfig.key == CONFIG_KEY_HISTORY)
    )
    config = result.scalar_one_or_none()

    if not config:
        return []

    history = config.value
    if isinstance(history, dict) and "items" in history:
        history = history["items"]

    if not isinstance(history, list):
        return []

    # Return most recent first
    return list(reversed(history))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_recipients(db: AsyncSession) -> list[dict]:
    """Load recipients list from SystemConfig."""
    result = await db.execute(
        select(SystemConfig).where(SystemConfig.key == CONFIG_KEY_RECIPIENTS)
    )
    config = result.scalar_one_or_none()
    if not config:
        return []

    value = config.value
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and "items" in value:
        return value["items"]
    return []


async def _set_config(db: AsyncSession, key: str, value: list) -> None:
    """Upsert a SystemConfig list value."""
    result = await db.execute(
        select(SystemConfig).where(SystemConfig.key == key)
    )
    config = result.scalar_one_or_none()
    if config:
        config.value = value
    else:
        config = SystemConfig(key=key, value=value)
        db.add(config)
    await db.commit()

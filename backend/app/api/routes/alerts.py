import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db
from app.auth.middleware import require_viewer, require_admin
from app.db.models import Alert, AlertStatus
from app.schemas.alert import AlertResponse, AlertAcknowledge

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=list[AlertResponse])
async def list_alerts(
    camera_id: uuid.UUID | None = None,
    status: AlertStatus | None = None,
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_viewer),
):
    stmt = select(Alert).order_by(Alert.timestamp.desc()).limit(limit)
    if camera_id is not None:
        stmt = stmt.where(Alert.camera_id == camera_id)
    if status is not None:
        stmt = stmt.where(Alert.status == status)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.patch("/{alert_id}", response_model=AlertResponse)
async def acknowledge_alert(
    alert_id: uuid.UUID,
    body: AlertAcknowledge,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    result = await db.execute(select(Alert).where(Alert.id == alert_id))
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.status = body.status
    await db.commit()
    await db.refresh(alert)
    return alert

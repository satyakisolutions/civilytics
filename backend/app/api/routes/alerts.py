import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db
from app.auth.middleware import require_viewer, require_admin
from app.auth.tenancy import get_tenant, scope
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
    tenant: str | None = Depends(get_tenant),
):
    stmt = scope(select(Alert).order_by(Alert.timestamp.desc()).limit(limit), Alert, tenant)
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
    # Acknowledging one alert is the control-room operator's primary action.
    # Gating it on `admin` left a viewer able to watch their queue fill but
    # not clear it. Bulk POST /clear stays admin -- that one is destructive.
    _user: dict = Depends(require_viewer),
    tenant: str | None = Depends(get_tenant),
):
    result = await db.execute(scope(select(Alert).where(Alert.id == alert_id), Alert, tenant))
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.status = body.status
    await db.commit()
    await db.refresh(alert)
    return alert


@router.post("/clear", status_code=200)
async def clear_all_alerts(
    db: AsyncSession = Depends(get_db),
    # SECURITY: bulk-resolves every active alert in one statement, including
    # ones no one has looked at, and cannot be undone. Previously this was
    # `require_viewer` while acknowledging a SINGLE alert required `admin` --
    # exactly backwards. Destructive bulk action stays admin-only.
    _user: dict = Depends(require_admin),
    tenant: str | None = Depends(get_tenant),
):
    """Resolve all active alerts in one bulk UPDATE (caller's tenant only;
    the tenantless owner clears across tenants)."""
    result = await db.execute(
        scope(update(Alert).where(Alert.status == "active"), Alert, tenant).values(status="resolved")
    )
    await db.commit()
    return {"cleared": result.rowcount}

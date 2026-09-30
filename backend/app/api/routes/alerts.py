import uuid
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db
from app.auth.middleware import require_viewer, require_admin
from app.db.models import Alert, AlertStatus
from app.schemas.alert import AlertResponse, AlertAcknowledge
from app.subjects.queries import alert_badges

router = APIRouter(prefix="/alerts", tags=["alerts"])

# An alert whose face incident has no result this long after firing is shown
# as "Face not captured" (request lost, pipeline restarted, worker down, ...),
# so every alert reaches a final badge state.
FACE_RESULT_TIMEOUT_S = 120


def _face_result_timed_out(ts: datetime | None, now: datetime) -> bool:
    if ts is None:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (now - ts).total_seconds() > FACE_RESULT_TIMEOUT_S


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
    alerts = result.scalars().all()
    badges = await alert_badges(db, [a.incident_id for a in alerts if a.incident_id is not None])
    now = datetime.now(timezone.utc)
    out = []
    for alert in alerts:
        item = AlertResponse.model_validate(alert)
        badge = badges.get(alert.incident_id) if alert.incident_id is not None else None
        if badge is not None:
            item = item.model_copy(update=asdict(badge))
        elif alert.incident_id is not None and _face_result_timed_out(alert.timestamp, now):
            item = item.model_copy(update={"face_status": "no_face"})
        out.append(item)
    return out


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


@router.post("/clear", status_code=200)
async def clear_all_alerts(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_viewer),
):
    """Resolve all active alerts in one bulk UPDATE."""
    result = await db.execute(
        update(Alert).where(Alert.status == "active").values(status="resolved")
    )
    await db.commit()
    return {"cleared": result.rowcount}

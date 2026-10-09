"""Workflow Operations API — health, workflows, workers, tasks, alerts, audit."""
import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.auth.middleware import require_admin, require_viewer
from app.db.workflow_models import (
    OpsAlert,
    OpsAlertDelivery,
    OpsAlertRule,
    WorkerHeartbeat,
    WorkflowActionAudit,
    WorkflowEventLink,
)
from app.schemas.workflow_ops import (
    AcknowledgeAlertRequest,
    AlertDeliveryOut,
    AlertOut,
    AlertRuleOut,
    AlertRuleUpdateRequest,
    AuditLogEntryOut,
    HealthSummaryOut,
    KafkaIntakeHealth,
    ResolveAlertRequest,
    RetryTaskRequest,
    ServiceHealth,
    WorkerHeartbeatOut,
    WorkflowEventLinkOut,
)
from app.workflow.conductor_client import conductor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/operations", tags=["operations"])


# ── Health summary ──


@router.get("/health-summary", response_model=HealthSummaryOut)
async def health_summary(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_viewer),
):
    conductor_health = ServiceHealth(status="unknown")
    try:
        h = await conductor.health()
        conductor_health = ServiceHealth(
            status="healthy" if h.get("healthy") else "degraded",
            last_check=datetime.now(timezone.utc),
        )
    except Exception as e:
        conductor_health = ServiceHealth(status="down", message=str(e)[:200])

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
    active_workers = (await db.execute(
        select(func.count()).where(WorkerHeartbeat.last_heartbeat_at >= cutoff)
    )).scalar_one()

    running = (await db.execute(
        select(func.count()).where(WorkflowEventLink.workflow_status == "RUNNING")
    )).scalar_one()

    day_ago = datetime.now(timezone.utc) - timedelta(hours=24)
    failed_24h = (await db.execute(
        select(func.count()).where(
            WorkflowEventLink.workflow_status == "FAILED",
            WorkflowEventLink.failed_at >= day_ago,
        )
    )).scalar_one()

    open_alerts = (await db.execute(
        select(func.count()).where(OpsAlert.status == "OPEN")
    )).scalar_one()

    return HealthSummaryOut(
        conductor=conductor_health,
        kafka=KafkaIntakeHealth(status="healthy"),
        active_workers=active_workers,
        running_workflows=running,
        failed_workflows_24h=failed_24h,
        open_alerts=open_alerts,
    )


# ── Workflows ──


@router.get("/workflows", response_model=list[WorkflowEventLinkOut])
async def list_workflows(
    status: str | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_viewer),
):
    q = select(WorkflowEventLink).order_by(WorkflowEventLink.created_at.desc())
    if status:
        q = q.where(WorkflowEventLink.workflow_status == status.upper())
    q = q.limit(limit).offset(offset)
    rows = (await db.execute(q)).scalars().all()
    return [WorkflowEventLinkOut.model_validate(r) for r in rows]


@router.get("/workflows/{workflow_id}")
async def get_workflow(
    workflow_id: str,
    _user: dict = Depends(require_viewer),
):
    try:
        return await conductor.get_workflow(workflow_id)
    except Exception as e:
        raise HTTPException(502, f"Conductor error: {e}") from e


@router.post("/workflows/{workflow_id}/retry", status_code=204)
async def retry_workflow(
    workflow_id: str,
    body: RetryTaskRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    try:
        await conductor.retry_workflow(workflow_id)
    except Exception as e:
        raise HTTPException(502, f"Conductor error: {e}") from e
    db.add(WorkflowActionAudit(
        actor_id=user.get("sub", "unknown"),
        actor_role=user.get("role", "admin"),
        action="RETRY_WORKFLOW",
        target_type="WORKFLOW",
        target_id=workflow_id,
        reason=body.reason,
        result="ACCEPTED",
    ))
    await db.commit()


@router.post("/workflows/{workflow_id}/terminate", status_code=204)
async def terminate_workflow(
    workflow_id: str,
    body: RetryTaskRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    try:
        await conductor.terminate_workflow(workflow_id, body.reason)
    except Exception as e:
        raise HTTPException(502, f"Conductor error: {e}") from e
    db.add(WorkflowActionAudit(
        actor_id=user.get("sub", "unknown"),
        actor_role=user.get("role", "admin"),
        action="TERMINATE_WORKFLOW",
        target_type="WORKFLOW",
        target_id=workflow_id,
        reason=body.reason,
        result="ACCEPTED",
    ))
    await db.commit()


# ── Workers ──


@router.get("/workers", response_model=list[WorkerHeartbeatOut])
async def list_workers(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_viewer),
):
    rows = (await db.execute(
        select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_heartbeat_at.desc())
    )).scalars().all()
    return [WorkerHeartbeatOut.model_validate(r) for r in rows]


# ── Failed tasks ──


@router.get("/tasks/failed")
async def list_failed_tasks(
    limit: int = Query(default=50, le=200),
    _user: dict = Depends(require_viewer),
):
    try:
        return await conductor.search_tasks("status=FAILED", start=0, size=limit)
    except Exception as e:
        raise HTTPException(502, f"Conductor error: {e}") from e


@router.post("/tasks/{task_id}/retry", status_code=204)
async def retry_task(
    task_id: str,
    body: RetryTaskRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    try:
        task = await conductor.get_task(task_id)
        wf_id = task.get("workflowInstanceId", "")
        if wf_id:
            await conductor.retry_workflow(wf_id)
    except Exception as e:
        raise HTTPException(502, f"Conductor error: {e}") from e
    db.add(WorkflowActionAudit(
        actor_id=user.get("sub", "unknown"),
        actor_role=user.get("role", "admin"),
        action="RETRY_TASK",
        target_type="TASK",
        target_id=task_id,
        reason=body.reason,
        result="ACCEPTED",
    ))
    await db.commit()


# ── Alerts ──


@router.get("/alerts", response_model=list[AlertOut])
async def list_alerts(
    status: str | None = None,
    severity: str | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_viewer),
):
    q = select(OpsAlert).order_by(OpsAlert.created_at.desc())
    if status:
        q = q.where(OpsAlert.status == status.upper())
    if severity:
        q = q.where(OpsAlert.severity == severity.upper())
    q = q.limit(limit).offset(offset)
    rows = (await db.execute(q)).scalars().all()
    return [AlertOut.model_validate(r) for r in rows]


@router.get("/alerts/{alert_id}", response_model=AlertOut)
async def get_alert(
    alert_id: str,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_viewer),
):
    row = (await db.execute(
        select(OpsAlert).where(OpsAlert.id == uuid.UUID(alert_id))
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Alert not found")
    return AlertOut.model_validate(row)


@router.post("/alerts/{alert_id}/acknowledge", status_code=204)
async def acknowledge_alert(
    alert_id: str,
    body: AcknowledgeAlertRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    uid = uuid.UUID(alert_id)
    result = await db.execute(
        update(OpsAlert)
        .where(OpsAlert.id == uid, OpsAlert.status == "OPEN")
        .values(
            status="ACKNOWLEDGED",
            acknowledged_at=datetime.now(timezone.utc),
            acknowledged_by=user.get("sub", "unknown"),
        )
    )
    if result.rowcount == 0:
        raise HTTPException(404, "Alert not found or already acknowledged")
    db.add(WorkflowActionAudit(
        actor_id=user.get("sub", "unknown"),
        actor_role=user.get("role", "admin"),
        action="ACKNOWLEDGE_ALERT",
        target_type="ALERT",
        target_id=alert_id,
        reason=body.note,
        result="ACKNOWLEDGED",
    ))
    await db.commit()


@router.post("/alerts/{alert_id}/resolve", status_code=204)
async def resolve_alert(
    alert_id: str,
    body: ResolveAlertRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    uid = uuid.UUID(alert_id)
    result = await db.execute(
        update(OpsAlert)
        .where(OpsAlert.id == uid, OpsAlert.status.in_(["OPEN", "ACKNOWLEDGED"]))
        .values(
            status="RESOLVED",
            resolved_at=datetime.now(timezone.utc),
            resolved_by=user.get("sub", "unknown"),
            resolution_note=body.resolution_note,
        )
    )
    if result.rowcount == 0:
        raise HTTPException(404, "Alert not found or already resolved")
    db.add(WorkflowActionAudit(
        actor_id=user.get("sub", "unknown"),
        actor_role=user.get("role", "admin"),
        action="RESOLVE_ALERT",
        target_type="ALERT",
        target_id=alert_id,
        reason=body.resolution_note,
        result="RESOLVED",
    ))
    await db.commit()


# ── Alert rules ──


@router.get("/alert-rules", response_model=list[AlertRuleOut])
async def list_alert_rules(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_viewer),
):
    rows = (await db.execute(
        select(OpsAlertRule).order_by(OpsAlertRule.created_at.desc())
    )).scalars().all()
    return [AlertRuleOut.model_validate(r) for r in rows]


@router.patch("/alert-rules/{rule_id}", response_model=AlertRuleOut)
async def update_alert_rule(
    rule_id: str,
    body: AlertRuleUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    uid = uuid.UUID(rule_id)
    row = (await db.execute(
        select(OpsAlertRule).where(OpsAlertRule.id == uid)
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Alert rule not found")
    updates = body.model_dump(exclude_unset=True)
    for k, v in updates.items():
        setattr(row, k, v)
    db.add(WorkflowActionAudit(
        actor_id=user.get("sub", "unknown"),
        actor_role=user.get("role", "admin"),
        action="UPDATE_ALERT_RULE",
        target_type="ALERT_RULE",
        target_id=rule_id,
        result="UPDATED",
    ))
    await db.commit()
    await db.refresh(row)
    return AlertRuleOut.model_validate(row)


# ── Alert deliveries ──


@router.get("/alerts/{alert_id}/deliveries", response_model=list[AlertDeliveryOut])
async def list_alert_deliveries(
    alert_id: str,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_viewer),
):
    rows = (await db.execute(
        select(OpsAlertDelivery)
        .where(OpsAlertDelivery.alert_id == uuid.UUID(alert_id))
        .order_by(OpsAlertDelivery.created_at.desc())
    )).scalars().all()
    return [AlertDeliveryOut.model_validate(r) for r in rows]


# ── Audit log ──


@router.get("/audit-log", response_model=list[AuditLogEntryOut])
async def list_audit_log(
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    action: str | None = None,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    q = select(WorkflowActionAudit).order_by(WorkflowActionAudit.created_at.desc())
    if action:
        q = q.where(WorkflowActionAudit.action == action.upper())
    q = q.limit(limit).offset(offset)
    rows = (await db.execute(q)).scalars().all()
    return [AuditLogEntryOut.model_validate(r) for r in rows]

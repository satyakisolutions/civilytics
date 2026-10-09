"""Workflow Operations API request/response schemas."""
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.workflow_contracts import (
    DeliveryChannel,
    OpsAlertSeverity,
    OpsAlertStatus,
    WorkflowStatus,
    WorkerStatus,
)


# ── Response schemas ──


class WorkflowEventLinkOut(BaseModel):
    id: str
    workspace_id: str
    event_id: str
    conductor_workflow_id: str
    workflow_name: str
    workflow_version: int
    correlation_id: str
    workflow_status: str
    started_at: datetime
    completed_at: datetime | None = None
    failed_at: datetime | None = None

    class Config:
        from_attributes = True


class WorkerHeartbeatOut(BaseModel):
    id: str
    worker_service: str
    task_type: str
    instance_id: str
    status: str
    last_heartbeat_at: datetime
    current_poll_count: int
    build_version: str | None = None

    class Config:
        from_attributes = True


class AlertOut(BaseModel):
    id: str
    rule_id: str | None = None
    workspace_id: str | None = None
    severity: str
    title: str
    description: str | None = None
    source_type: str
    source_id: str | None = None
    status: str
    first_triggered_at: datetime
    last_triggered_at: datetime
    acknowledged_at: datetime | None = None
    acknowledged_by: str | None = None
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    resolution_note: str | None = None

    class Config:
        from_attributes = True


class AlertRuleOut(BaseModel):
    id: str
    workspace_id: str | None = None
    name: str
    source_type: str
    condition_type: str
    threshold_value: float | None = None
    window_minutes: int
    severity: str
    delivery_channels: list[str]
    cooldown_minutes: int
    enabled: bool

    class Config:
        from_attributes = True


class AlertDeliveryOut(BaseModel):
    id: str
    alert_id: str
    channel: str
    recipient: str
    delivery_status: str
    delivered_at: datetime | None = None

    class Config:
        from_attributes = True


class AuditLogEntryOut(BaseModel):
    id: str
    actor_id: str
    actor_role: str
    action: str
    target_type: str
    target_id: str
    reason: str | None = None
    result: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True


# ── Request schemas ──


class RetryTaskRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class AcknowledgeAlertRequest(BaseModel):
    note: str | None = None


class ResolveAlertRequest(BaseModel):
    resolution_note: str = Field(min_length=1, max_length=2000)


class AssignAlertRequest(BaseModel):
    assignee_id: str


class AlertRuleUpdateRequest(BaseModel):
    enabled: bool | None = None
    threshold_value: float | None = None
    window_minutes: int | None = None
    severity: str | None = None
    delivery_channels: list[str] | None = None
    cooldown_minutes: int | None = None


# ── Health summary ──


class ServiceHealth(BaseModel):
    status: str  # healthy / degraded / down
    message: str | None = None
    last_check: datetime | None = None


class KafkaIntakeHealth(BaseModel):
    status: str
    consumer_group: str | None = None
    latest_consumed_offset: int | None = None
    committed_offset: int | None = None
    processing_lag: int | None = None
    dlq_count: int = 0


class HealthSummaryOut(BaseModel):
    conductor: ServiceHealth
    kafka: KafkaIntakeHealth
    active_workers: int = 0
    running_workflows: int = 0
    failed_workflows_24h: int = 0
    open_alerts: int = 0

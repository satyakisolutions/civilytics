"""Workflow Operations domain contracts — shared between intake, workers, and API."""
import enum
from datetime import datetime

from pydantic import BaseModel, Field


class DetectionType(str, enum.Enum):
    MIDNIGHT_WALK = "MIDNIGHT_WALK"
    MIDNIGHT_VEHICLE = "MIDNIGHT_VEHICLE"
    DAYTIME_WALK = "DAYTIME_WALK"
    DAYTIME_VEHICLE = "DAYTIME_VEHICLE"


class EventPriority(str, enum.Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ProcessingStatus(str, enum.Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"


class WorkflowStatus(str, enum.Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    PAUSED = "PAUSED"
    TERMINATED = "TERMINATED"


class OpsAlertSeverity(str, enum.Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class OpsAlertStatus(str, enum.Enum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class AlertSourceType(str, enum.Enum):
    WORKFLOW = "WORKFLOW"
    TASK = "TASK"
    WORKER_HEARTBEAT = "WORKER_HEARTBEAT"
    KAFKA_INTAKE = "KAFKA_INTAKE"
    DEAD_LETTER = "DEAD_LETTER"
    CAMERA_HEALTH = "CAMERA_HEALTH"
    EVIDENCE_STORAGE = "EVIDENCE_STORAGE"
    APP_HEALTH = "APP_HEALTH"


class DeliveryChannel(str, enum.Enum):
    IN_APP = "IN_APP"
    EMAIL = "EMAIL"
    WEBHOOK = "WEBHOOK"


class WorkerStatus(str, enum.Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"


# ── Task type registry (single source of truth) ──

TASK_TYPES = [
    "validate_dumping_event",
    "extract_evidence",
    "run_lpr_analysis",
    "cluster_subject_sightings",
    "update_analytics",
    "publish_event_record",
    "notify_workflow_failure",
]

WORKFLOW_NAME = "dumping_event_processing"
WORKFLOW_VERSION = 1


# ── Domain input/output contracts ──

class DumpingEventInput(BaseModel):
    event_id: str
    event_occurred_at: datetime
    workspace_id: str
    zone_id: str
    camera_id: str
    camera_location: str
    detection_type: DetectionType
    confidence: float = Field(ge=0, le=1)
    priority: EventPriority
    source_video_ref: str
    source_frame_ref: str
    correlation_id: str
    schema_version: int = 1
    bounding_boxes: list[dict] | None = None
    object_count: int | None = None
    vehicle_visible: bool | None = None
    person_visible: bool | None = None
    evidence_hints: dict | None = None
    source_event_id: str | None = None


class DumpingEventWorkflowOutput(BaseModel):
    event_id: str
    workflow_id: str
    processing_status: ProcessingStatus
    evidence_status: str | None = None
    evidence_refs: list[str] = []
    lpr_result: dict | None = None
    cluster_result: dict | None = None
    analytics_status: str | None = None
    published_at: datetime | None = None
    warnings: list[str] = []

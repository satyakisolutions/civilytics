import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


class CameraStatus(str, enum.Enum):
    online = "online"
    offline = "offline"
    warning = "warning"
    alert = "alert"


class AlertSeverity(str, enum.Enum):
    critical = "critical"
    warning = "warning"
    info = "info"


class AlertStatus(str, enum.Enum):
    active = "active"
    acknowledged = "acknowledged"
    resolved = "resolved"


class EventType(str, enum.Enum):
    dumping_detected = "dumping_detected"
    suspicious_activity = "suspicious_activity"
    repeat_offender = "repeat_offender"


class AppRole(str, enum.Enum):
    owner = "owner"
    admin = "admin"
    viewer = "viewer"


class Base(DeclarativeBase):
    pass


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    zone: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    gps_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    gps_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[CameraStatus] = mapped_column(
        Enum(CameraStatus, create_type=False), default=CameraStatus.offline, nullable=False
    )
    stream_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(String(10), default="rtsp", nullable=False)
    thumbnail_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    roi_polygons: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    recording_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Per-camera overrides for waste-dump detection thresholds. Shape:
    #   {"dwell_violation_threshold_s": float, "waste_proximity_alert_window_s": float,
    #    "waste_proximity_px": float}
    # Any key omitted falls back to the pipeline's global default (see
    # pipeline/app/config.py's PipelineSettings). Read by the pipeline via
    # ROISyncer's existing /api/cameras poll (pipeline/app/roi_sync.py).
    detection_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ViolationEvent(Base):
    __tablename__ = "violation_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    camera_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cameras.id"), nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_type: Mapped[EventType] = mapped_column(
        Enum(EventType, create_type=False), nullable=False
    )
    severity: Mapped[AlertSeverity] = mapped_column(
        Enum(AlertSeverity, create_type=False), nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    track_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dwell_time_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    vlm_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    person_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    repeat_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    snapshot_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    video_clip_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[AlertStatus] = mapped_column(
        Enum(AlertStatus, create_type=False), default=AlertStatus.active, nullable=False
    )
    acknowledged_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_valid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("violation_events.id"), nullable=True
    )
    camera_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cameras.id"), nullable=True
    )
    severity: Mapped[AlertSeverity] = mapped_column(
        Enum(AlertSeverity, create_type=False), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    zone: Mapped[str | None] = mapped_column(String(255), nullable=True)
    channel: Mapped[str | None] = mapped_column(String(50), nullable=True)
    tts_audio_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # URL (served under /evidence, see backend/app/main.py's StaticFiles
    # mount) of the frame this alert fired on. Set by alert_consumer.py
    # from the pipeline's send_alert(..., snapshot_url=...) payload.
    snapshot_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    status: Mapped[AlertStatus] = mapped_column(
        Enum(AlertStatus, create_type=False), default=AlertStatus.active, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SystemConfig(Base):
    __tablename__ = "system_config"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TrackState(str, enum.Enum):
    active = "active"
    lost = "lost"
    exited = "exited"


class EventFSMState(str, enum.Enum):
    enter = "enter"
    observe = "observe"
    violation = "violation"
    alerting = "alerting"
    escalated = "escalated"
    exited = "exited"


class TrackSession(Base):
    __tablename__ = "track_sessions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    camera_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cameras.id"))
    track_id: Mapped[int] = mapped_column(Integer)
    fsm_state: Mapped[EventFSMState] = mapped_column(Enum(EventFSMState, name="event_fsm_state", create_type=False), default=EventFSMState.enter)
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    exited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dwell_time_s: Mapped[float | None] = mapped_column(Float)
    bbox_history: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VLMAnalysis(Base):
    __tablename__ = "vlm_analyses"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    track_session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("track_sessions.id"))
    camera_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cameras.id"))
    frames_analyzed: Mapped[int] = mapped_column(Integer)
    prompt_used: Mapped[str | None] = mapped_column(Text)
    response: Mapped[dict] = mapped_column(JSONB)
    inference_time_ms: Mapped[int] = mapped_column(Integer)
    model_version: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AlertDelivery(Base):
    __tablename__ = "alert_deliveries"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    alert_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("alerts.id"))
    channel: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(50), default="pending")
    payload: Mapped[dict | None] = mapped_column(JSONB)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlateRead(Base):
    __tablename__ = "plate_reads"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    camera_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cameras.id"))
    track_session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("track_sessions.id"))
    plate_number: Mapped[str] = mapped_column(String(20))
    confidence: Mapped[float] = mapped_column(Float)
    plate_image_url: Mapped[str | None] = mapped_column(Text)
    vehicle_bbox: Mapped[dict | None] = mapped_column(JSONB)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TTSCacheEntry(Base):
    __tablename__ = "tts_cache"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    text_hash: Mapped[str] = mapped_column(String(64), unique=True)
    text: Mapped[str] = mapped_column(Text)
    audio_url: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvidenceFile(Base):
    __tablename__ = "evidence_files"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("violation_events.id"))
    file_type: Mapped[str] = mapped_column(String(50))  # snapshot, video_clip, annotated_snapshot, metadata_json
    local_path: Mapped[str | None] = mapped_column(Text)
    dropbox_path: Mapped[str | None] = mapped_column(Text)
    sync_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending, uploading, synced, failed
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LogEntry(Base):
    __tablename__ = "log_entries"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service: Mapped[str] = mapped_column(String(100))
    level: Mapped[str] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict | None] = mapped_column("metadata", JSONB)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

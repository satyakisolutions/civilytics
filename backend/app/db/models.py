import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy import text as sa_text


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


# NOTE: there is deliberately no AppRole enum here. Keycloak is the authority
# on roles -- require_role() matches the token claim, and the owner/admin/
# viewer hierarchy is expressed as Keycloak COMPOSITE roles plus _GRANTABLE in
# api/routes/users.py. A second definition in the database would have no
# enforcement path and would drift from the one that does.


class DeliveryStatus(str, enum.Enum):
    """Outcome of one delivery attempt on one channel."""

    pending = "pending"
    sent = "sent"
    failed = "failed"


class SyncStatus(str, enum.Enum):
    """Backup state of one evidence file."""

    pending = "pending"
    uploading = "uploading"
    synced = "synced"
    failed = "failed"


class RemoteBackend(str, enum.Enum):
    """Where a backed-up evidence file went."""

    gdrive = "gdrive"
    dropbox = "dropbox"
    s3 = "s3"


class AckSource(str, enum.Enum):
    """How an acknowledgement arrived.

    NOT equivalent trust. `dashboard` is an authenticated session tied to a
    named user. `ivr` is whoever answered the phone and pressed a key --
    a mis-dialled number or a shared desk phone can produce one. Recording
    them identically would make an unattributable ack indistinguishable from
    an operator's, on a record a prosecution may rely on.
    """

    dashboard = "dashboard"
    ivr = "ivr"
    api = "api"


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    """One customer. One admin == one customer.

    The tenant id also lives as the `ww_tenant` Keycloak user attribute, which
    is what authorisation reads. This table is what lets it be JOINed -- the
    Admin API cannot be joined, and a per-request lookup to list one
    customer's cameras would be an N+1 on every page load.
    """

    __tablename__ = "tenants"

    # Must be the SAME uuid minted into ww_tenant when the owner creates the
    # admin, or the token claim cannot be joined to anything here.
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    # A lapsed contract must stop alerting WITHOUT deleting the row: deleting
    # would cascade away evidence that is a legal record.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Evidence retention is a contractual term and differs per customer.
    retention_days: Mapped[int] = mapped_column(Integer, default=90, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Zone(Base):
    """A named area within one customer's deployment.

    Replaces the free-text `cameras.zone` / `alerts.zone`. Two reasons, one of
    them a safety bug:

      1. A zone name is SPOKEN over a public address speaker. Deciding
         whether a string is safe to say out loud cannot be done with a
         regex -- the existing character-class guard passes "near the man in
         the blue jacket", 31 characters of letters and spaces. `is_speakable`
         moves that decision to a human, once, when the zone is created.
      2. Renaming a free-text zone leaves every historical row holding the old
         string, silently forking one zone into two in every rollup.
    """

    __tablename__ = "zones"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_zones_tenant_name"),
        Index("ix_zones_tenant_id", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # THE SAFETY GATE for public announcements. Default False: a zone is not
    # speakable until someone says it is. Defaulting True would make the
    # unsafe case the one you get by doing nothing.
    is_speakable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # DBSCAN time radius and min_samples, PER ZONE -- the real figures differ
    # by site (Adugodi 45/8, Lakkasandra 60/4, Hongasandra 45/6). One global
    # value mis-clusters every site but one.
    eps_minutes: Mapped[int] = mapped_column(Integer, default=45, nullable=False)
    min_events: Mapped[int] = mapped_column(Integer, default=6, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SchedulerJob(Base):
    """Live cadence for one scheduler job, editable from the UI.

    This table exists because a screen cannot edit a Python constant. It is
    NOT load-bearing: each job also carries a default in the code registry,
    and an unreachable database falls back to it. If the interval existed
    only here, a DB blip at startup would mean no background job runs at all.

    `min_interval_s` is deliberately absent. It is the floor that stops
    someone setting the categoriser to 1s and stalling the loop, so it lives
    in code where the UI cannot raise it.
    """

    __tablename__ = "scheduler_jobs"

    # Natural key -- the registry looks jobs up by name ("ops/healer").
    job: Mapped[str] = mapped_column(String(64), primary_key=True)
    interval_s: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # "Who disabled the alerts job" is asked after an incident, not before.
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Camera(Base):
    __tablename__ = "cameras"
    __table_args__ = (
        # Two LIVE cameras with the same name in one customer makes the
        # MediaMTX path ambiguous. Partial (deleted_at IS NULL) so a
        # soft-deleted camera does not permanently burn its name — otherwise
        # deleting "Cam1" blocks ever creating a "Cam1" again.
        Index(
            "uq_cameras_tenant_name", "tenant_id", "name",
            unique=True, postgresql_where=sa_text("deleted_at IS NULL"),
        ),
        # A swapped lat/lng pair puts the camera in the Arabian Sea, and both
        # numbers look plausible for Bengaluru. Half a coordinate is worse
        # than none: it renders at lng=0 instead of being flagged as missing.
        CheckConstraint("gps_lat IS NULL OR (gps_lat BETWEEN -90 AND 90)", name="ck_cameras_lat"),
        CheckConstraint("gps_lng IS NULL OR (gps_lng BETWEEN -180 AND 180)", name="ck_cameras_lng"),
        CheckConstraint("(gps_lat IS NULL) = (gps_lng IS NULL)", name="ck_cameras_gps_pair"),
        Index("ix_cameras_tenant_status", "tenant_id", "status"),
        Index("ix_cameras_zone_id", "zone_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # The single column that makes tenancy possible. Without it /api/cameras
    # cannot be scoped and an admin from customer A reads customer B's
    # cameras with a perfectly valid token.
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    zone_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("zones.id"), nullable=False
    )
    # Eager-loaded: every camera read renders its zone name, so lazy loading
    # would be an N+1 on the dashboard's twelve tiles.
    zone_rel: Mapped["Zone"] = relationship(lazy="selectin")
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
    # Soft-delete stamp. Cameras accrue alerts/events/plate-reads that must
    # survive the camera's removal (enforcement record), so DELETE /api/cameras
    # does not drop the row — it stamps deleted_at and every read filters it
    # out. The FK-protected history stays queryable by its camera_id.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def zone(self) -> str | None:
        """Zone name, so the API contract keeps exposing `zone` as a string.

        The column became a FK (see Zone), but callers, the pipeline command
        payload and the dashboard all speak zone names. Resolving here keeps
        the storage normalised without a breaking API change.
        """
        return self.zone_rel.name if self.zone_rel else None


class ViolationEvent(Base):
    __tablename__ = "violation_events"
    __table_args__ = (
        # The history query.
        Index("ix_violation_events_camera_timestamp", "camera_id", "timestamp"),
        # The review queue: only unreviewed rows, so the index stays small
        # however large the table grows.
        Index(
            "ix_violation_events_unreviewed",
            "timestamp",
            postgresql_where=sa_text("is_valid IS NULL"),
        ),
        # The categoriser clusters on time of day across all cameras.
        Index("ix_violation_events_timestamp", "timestamp"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Tenancy (T1). Nullable: worker inserts land NULL until the worker layer
    # stamps them. See app/auth/tenancy.py for the request-side scoping.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=True, index=True
    )
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
    # Stored, not recomputed: the threshold this was judged against changes
    # over time, and a historical event must keep the value it was judged on.
    dwell_time_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Renamed from vlm_response. The VLM was removed in favour of the
    # relation model; keeping the old name would make the column lie about
    # what produced it.
    relation_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    person_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    repeat_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Derived category: midnight-work, midnight-vehicle, daytime-work,
    # daytime-vehicle. NULLABLE ON PURPOSE -- an event at an odd hour, or in
    # a cluster below the zone's min_events, is recorded and NOT categorised.
    # A NOT NULL here would force every outlier into a bucket, which is the
    # opposite of what the clustering is for.
    category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # person / vehicle. The second clustering axis ("vehicle-based vs
    # work-based dumping") and not derivable from event_type.
    actor_class: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Which categoriser run assigned the category. Without it, re-clustering
    # silently rewrites history and nobody can tell which events moved.
    category_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    snapshot_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    video_clip_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[AlertStatus] = mapped_column(
        Enum(AlertStatus, create_type=False), default=AlertStatus.active, nullable=False
    )
    acknowledged_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # THREE-STATE on purpose: NULL = not reviewed, True = genuine, False =
    # false positive. A Boolean NOT NULL would conflate "nobody looked" with
    # "it was fine", and this column is the training signal for every future
    # model -- that distinction is the whole value of it.
    is_valid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_alerts_camera_id", "camera_id"),
        Index("ix_alerts_event_id", "event_id"),
        Index("ix_alerts_zone_id", "zone_id"),
        # The dashboard's "what is live right now" query.
        Index(
            "ix_alerts_active",
            "timestamp",
            postgresql_where=sa_text("status = 'active'::alertstatus"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Tenancy (T1). Nullable: worker inserts land NULL until stamped.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=True, index=True
    )
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
    zone_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("zones.id"), nullable=True
    )
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
    # Acknowledgement attribution. These were missing entirely while the UI
    # acknowledges ALERTS (PATCH /api/alerts/{id}) -- violation_events had
    # the columns, the table actually written did not. For an enforcement
    # product this is the chain of custody.
    #
    # String, not a FK: the user may later be deleted from Keycloak and the
    # attribution has to survive them.
    acknowledged_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ack_source: Mapped[AckSource | None] = mapped_column(
        Enum(AckSource, name="ack_source", create_type=False), nullable=True
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
    __table_args__ = (
        # entered_at is in the key because nvtracker IDS ARE REUSED --
        # maxShadowTrackingAge: 50 recycles an id ~2s after a track dies, so
        # (camera_id, track_id) alone collides two different people onto one
        # session.
        UniqueConstraint(
            "camera_id", "track_id", "entered_at", name="uq_track_sessions_identity"
        ),
        Index("ix_track_sessions_camera_id", "camera_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Tenancy (T1). Nullable: worker inserts land NULL until stamped.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=True, index=True
    )
    camera_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cameras.id"))
    track_id: Mapped[int] = mapped_column(Integer)
    fsm_state: Mapped[EventFSMState] = mapped_column(Enum(EventFSMState, name="event_fsm_state", create_type=False), default=EventFSMState.enter)
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    exited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dwell_time_s: Mapped[float | None] = mapped_column(Float)
    # Track path, for the relation model and for replaying a disputed event
    # without the video. CAP THIS on write -- an actor loitering for an hour
    # at 15 fps is 54,000 boxes in a single JSONB value.
    bbox_history: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# VLMAnalysis was removed. It described a vision-language model that no
# longer exists -- frames_analyzed, prompt_used, inference_time_ms and
# model_version are all properties of the VLM, not of the relation model that
# replaced it. The relation model produces one structured verdict per event,
# which belongs on violation_events.relation_result rather than in a separate
# analysis document.


class AlertDelivery(Base):
    __tablename__ = "alert_deliveries"
    __table_args__ = (Index("ix_alert_deliveries_alert_id", "alert_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    alert_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("alerts.id"))
    channel: Mapped[str] = mapped_column(String(50))
    # Enum, not a free string: "failed"/"Failed"/"failure" coexisting makes
    # the retry query quietly wrong.
    status: Mapped[DeliveryStatus] = mapped_column(
        Enum(DeliveryStatus, name="delivery_status", create_type=False),
        default=DeliveryStatus.pending,
    )
    payload: Mapped[dict | None] = mapped_column(JSONB)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlateRead(Base):
    __tablename__ = "plate_reads"
    __table_args__ = (
        Index("ix_plate_reads_camera_id", "camera_id"),
        Index("ix_plate_reads_track_session_id", "track_session_id"),
        # Repeat-offender lookup runs on the normalised form.
        Index("ix_plate_reads_normalised", "plate_normalised"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Tenancy (T1). Nullable: worker inserts land NULL until stamped.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=True, index=True
    )
    camera_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cameras.id"))
    track_session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("track_sessions.id"))
    # As read, verbatim. Kept raw because it is evidence.
    plate_number: Mapped[str] = mapped_column(String(20))
    # Uppercased, whitespace and separators stripped. Matching on the raw
    # string treats "KA 01 AB 1234" and "KA01AB1234" as two vehicles, which
    # silently defeats the one thing this table exists for.
    plate_normalised: Mapped[str] = mapped_column(String(16))
    # A plate read at 0.4 should not convict anyone.
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
    __table_args__ = (
        Index("ix_evidence_files_event_id", "event_id"),
        # The backup scheduler's work queue.
        Index(
            "ix_evidence_files_unsynced",
            "created_at",
            postgresql_where=sa_text("sync_status <> 'synced'::sync_status"),
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Tenancy (T1). Nullable: worker inserts land NULL until stamped.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=True, index=True
    )
    event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("violation_events.id"))
    # snapshot / video_clip / annotated_snapshot / metadata_json.
    # annotated_snapshot MUST stay distinct from snapshot: "no bbox on the
    # person" means the clean capture and the annotated review copy are two
    # artefacts, and conflating them puts burned-in boxes on a court exhibit.
    file_type: Mapped[str] = mapped_column(String(50))
    local_path: Mapped[str | None] = mapped_column(Text)
    # Renamed from dropbox_path: the backup target is GDrive, Dropbox or S3,
    # so a vendor name in a column name forces a migration per provider.
    remote_path: Mapped[str | None] = mapped_column(Text)
    remote_backend: Mapped[RemoteBackend | None] = mapped_column(
        Enum(RemoteBackend, name="remote_backend", create_type=False), nullable=True
    )
    sync_status: Mapped[SyncStatus] = mapped_column(
        Enum(SyncStatus, name="sync_status", create_type=False), default=SyncStatus.pending
    )
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # SHA-256 at capture. Evidence whose integrity cannot be demonstrated is
    # weak evidence, and this is also the only way to prove a remote copy
    # still matches the local one.
    checksum: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LogEntry(Base):
    __tablename__ = "log_entries"
    __table_args__ = (
        Index("ix_log_entries_service_timestamp", "service", "timestamp"),
        # Following one browser session through a bug report. Expression
        # index because session_id lives in the JSONB rather than its own
        # column -- log_entries predates browser capture and adding a column
        # would mean rewriting the table.
        Index(
            "ix_log_entries_session_id",
            sa_text("(metadata->>'session_id')"),
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Tenancy (T1). Nullable: worker/service inserts land NULL until stamped.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=True, index=True
    )
    service: Mapped[str] = mapped_column(String(100))
    level: Mapped[str] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict | None] = mapped_column("metadata", JSONB)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

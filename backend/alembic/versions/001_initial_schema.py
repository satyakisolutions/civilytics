"""Initial schema: cameras, violation_events, alerts, system_config

Revision ID: 001
Revises:
Create Date: 2026-04-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Enums (idempotent via DO block; ENUM.create(checkfirst=True) is broken on asyncpg) ---
    def _create_enum(name: str, values: tuple[str, ...]) -> None:
        literals = ",".join(f"'{v}'" for v in values)
        op.execute(
            f"DO $$ BEGIN CREATE TYPE {name} AS ENUM ({literals}); "
            f"EXCEPTION WHEN duplicate_object THEN null; END $$;"
        )

    _create_enum("camerastatus", ("online", "offline", "warning", "alert"))
    _create_enum("alertseverity", ("critical", "warning", "info"))
    _create_enum("alertstatus", ("active", "acknowledged", "resolved"))
    _create_enum("eventtype", ("dumping_detected", "suspicious_activity", "repeat_offender"))
    _create_enum("approle", ("owner", "admin", "viewer"))
    _create_enum("eventfsmstate", ("idle", "detecting", "tracking", "confirming", "alerting", "cooldown"))

    # --- cameras ---
    op.create_table(
        "cameras",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("zone", sa.String(255), nullable=False),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("gps_lat", sa.Float(), nullable=True),
        sa.Column("gps_lng", sa.Float(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM("online", "offline", "warning", "alert", name="camerastatus", create_type=False),
            nullable=False,
            server_default="offline",
        ),
        sa.Column("stream_url", sa.Text(), nullable=True),
        sa.Column("source_type", sa.String(10), nullable=False, server_default="rtsp"),
        sa.Column("thumbnail_url", sa.Text(), nullable=True),
        sa.Column("roi_polygons", postgresql.JSONB(), nullable=True),
        sa.Column("recording_config", postgresql.JSONB(), nullable=True),
        sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # --- violation_events ---
    op.create_table(
        "violation_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "camera_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cameras.id"),
            nullable=False,
        ),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "event_type",
            postgresql.ENUM(
                "dumping_detected", "suspicious_activity", "repeat_offender",
                name="eventtype",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "severity",
            postgresql.ENUM("critical", "warning", "info", name="alertseverity", create_type=False),
            nullable=False,
        ),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=True),
        sa.Column("dwell_time_s", sa.Float(), nullable=True),
        sa.Column("vlm_response", postgresql.JSONB(), nullable=True),
        sa.Column("person_id", sa.String(255), nullable=True),
        sa.Column("repeat_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("snapshot_url", sa.Text(), nullable=True),
        sa.Column("video_clip_url", sa.Text(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM("active", "acknowledged", "resolved", name="alertstatus", create_type=False),
            nullable=False,
            server_default="active",
        ),
        sa.Column("acknowledged_by", sa.String(255), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_valid", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index(
        "ix_violation_events_camera_id_timestamp",
        "violation_events",
        ["camera_id", sa.text("timestamp DESC")],
    )

    # --- alerts ---
    op.create_table(
        "alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("violation_events.id"),
            nullable=True,
        ),
        sa.Column(
            "camera_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cameras.id"),
            nullable=True,
        ),
        sa.Column(
            "severity",
            postgresql.ENUM("critical", "warning", "info", name="alertseverity", create_type=False),
            nullable=False,
        ),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("zone", sa.String(255), nullable=True),
        sa.Column("channel", sa.String(50), nullable=True),
        sa.Column("tts_audio_url", sa.Text(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "status",
            postgresql.ENUM("active", "acknowledged", "resolved", name="alertstatus", create_type=False),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # --- system_config ---
    op.create_table(
        "system_config",
        sa.Column("key", sa.String(255), primary_key=True),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # --- Seed default config values ---
    op.execute(
        sa.text(
            """
            INSERT INTO system_config (key, value) VALUES
                ('detection.frame_skip',             '3'),
                ('detection.batch_size',             '4'),
                ('detection.confidence_thresholds',  '{"person": 0.5, "bicycle": 0.4, "car": 0.5, "motorcycle": 0.5, "bus": 0.5, "truck": 0.5}'),
                ('tracking.dwell_threshold_s',       '5'),
                ('tracking.lost_timeout_s',          '30'),
                ('vlm.trigger_dwell_s',              '15'),
                ('vlm.max_concurrent',               '2'),
                ('alerts.cooldown_s',                '60'),
                ('alerts.max_per_hour',              '10'),
                ('recording.pre_buffer_s',           '15'),
                ('recording.post_buffer_s',          '30')
            ON CONFLICT (key) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.drop_table("alerts")
    op.drop_table("violation_events")
    op.drop_table("cameras")
    op.drop_table("system_config")

    op.execute("DROP TYPE IF EXISTS eventfsmstate")
    op.execute("DROP TYPE IF EXISTS approle")
    op.execute("DROP TYPE IF EXISTS eventtype")
    op.execute("DROP TYPE IF EXISTS alertstatus")
    op.execute("DROP TYPE IF EXISTS alertseverity")
    op.execute("DROP TYPE IF EXISTS camerastatus")

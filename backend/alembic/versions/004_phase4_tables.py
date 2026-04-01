"""Add plate_reads, tts_cache, evidence_files tables

Revision ID: 004
Revises: 003
Create Date: 2026-04-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- plate_reads table ---
    op.create_table(
        "plate_reads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "camera_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cameras.id"),
            nullable=False,
        ),
        sa.Column(
            "track_session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("track_sessions.id"),
            nullable=True,
        ),
        sa.Column("plate_number", sa.String(20), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("plate_image_url", sa.Text(), nullable=True),
        sa.Column("vehicle_bbox", postgresql.JSONB(), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_plate_reads_plate_number_timestamp",
        "plate_reads",
        ["plate_number", sa.text("timestamp DESC")],
    )
    op.create_index(
        "ix_plate_reads_camera_id_timestamp",
        "plate_reads",
        ["camera_id", sa.text("timestamp DESC")],
    )

    # --- tts_cache table ---
    op.create_table(
        "tts_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("text_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("audio_url", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_tts_cache_text_hash",
        "tts_cache",
        ["text_hash"],
        unique=True,
    )

    # --- evidence_files table ---
    op.create_table(
        "evidence_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("violation_events.id"),
            nullable=True,
        ),
        sa.Column("file_type", sa.String(50), nullable=False),
        sa.Column("local_path", sa.Text(), nullable=True),
        sa.Column("dropbox_path", sa.Text(), nullable=True),
        sa.Column("sync_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("evidence_files")
    op.drop_index("ix_tts_cache_text_hash", table_name="tts_cache")
    op.drop_table("tts_cache")
    op.drop_index("ix_plate_reads_camera_id_timestamp", table_name="plate_reads")
    op.drop_index("ix_plate_reads_plate_number_timestamp", table_name="plate_reads")
    op.drop_table("plate_reads")

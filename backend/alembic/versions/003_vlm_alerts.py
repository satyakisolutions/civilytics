"""Add vlm_analyses and alert_deliveries tables

Revision ID: 003
Revises: 002
Create Date: 2026-04-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- vlm_analyses table ---
    op.create_table(
        "vlm_analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "track_session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("track_sessions.id"),
            nullable=True,
        ),
        sa.Column(
            "camera_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cameras.id"),
            nullable=False,
        ),
        sa.Column("frames_analyzed", sa.Integer(), nullable=False),
        sa.Column("prompt_used", sa.Text(), nullable=True),
        sa.Column("response", postgresql.JSONB(), nullable=False),
        sa.Column("inference_time_ms", sa.Integer(), nullable=False),
        sa.Column("model_version", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # --- alert_deliveries table ---
    op.create_table(
        "alert_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "alert_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("alerts.id"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(50), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # Partial index on alert_deliveries(status) for non-sent rows
    op.create_index(
        "ix_alert_deliveries_status_pending",
        "alert_deliveries",
        ["status"],
        postgresql_where=sa.text("status != 'sent'"),
    )


def downgrade() -> None:
    op.drop_index("ix_alert_deliveries_status_pending", table_name="alert_deliveries")
    op.drop_table("alert_deliveries")
    op.drop_table("vlm_analyses")

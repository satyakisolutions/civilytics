"""Add track_sessions table

Revision ID: 002
Revises: 001
Create Date: 2026-04-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- track_state enum ---
    track_state = postgresql.ENUM(
        "active", "lost", "exited",
        name="track_state",
    )
    track_state.create(op.get_bind(), checkfirst=True)

    # --- event_fsm_state enum (new, distinct from eventfsmstate in migration 001) ---
    event_fsm_state = postgresql.ENUM(
        "enter", "observe", "violation", "alerting", "escalated", "exited",
        name="event_fsm_state",
    )
    event_fsm_state.create(op.get_bind(), checkfirst=True)

    # --- track_sessions table ---
    op.create_table(
        "track_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "camera_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cameras.id"),
            nullable=False,
        ),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column(
            "fsm_state",
            sa.Enum(
                "enter", "observe", "violation", "alerting", "escalated", "exited",
                name="event_fsm_state",
                create_type=False,
            ),
            nullable=False,
            server_default="enter",
        ),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dwell_time_s", sa.Float(), nullable=True),
        sa.Column("bbox_history", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index(
        "ix_track_sessions_camera_id_entered_at",
        "track_sessions",
        ["camera_id", sa.text("entered_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_track_sessions_camera_id_entered_at", table_name="track_sessions")
    op.drop_table("track_sessions")
    op.execute("DROP TYPE IF EXISTS event_fsm_state")
    op.execute("DROP TYPE IF EXISTS track_state")

"""Face-based subject IDs: subjects, faces, incidents, reviews, access log; alerts.incident_id

Revision ID: 008
Revises: 007
Create Date: 2026-09-29 00:00:00.000000

See docs/superpowers/specs/2026-09-29-face-subject-id-design.md §7.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)


def _ts(name: str, nullable: bool = False, default: bool = False) -> sa.Column:
    return sa.Column(
        name, sa.DateTime(timezone=True), nullable=nullable,
        server_default=sa.func.now() if default else None,
    )


def upgrade() -> None:
    op.execute("CREATE SEQUENCE IF NOT EXISTS subject_code_seq START 1")
    op.create_table(
        "subjects",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("code_num", sa.Integer(), nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("merged_into_id", UUID, sa.ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("incident_count", sa.Integer(), nullable=False, server_default="0"),
        _ts("first_seen_at"),
        _ts("last_seen_at"),
        sa.Column("cover_face_id", UUID, nullable=True),
        _ts("created_at", default=True),
    )
    op.create_table(
        "subject_faces",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("subject_id", UUID, sa.ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("incident_id", UUID, nullable=False),
        sa.Column("crop_path", sa.Text(), nullable=False),
        sa.Column("quality", sa.Float(), nullable=False),
        sa.Column("embedding", sa.LargeBinary(), nullable=False),
        sa.Column("qdrant_synced", sa.Boolean(), nullable=False, server_default=sa.false()),
        _ts("created_at", default=True),
    )
    op.create_index("ix_subject_faces_subject_id", "subject_faces", ["subject_id"])
    op.create_index("ix_subject_faces_incident_id", "subject_faces", ["incident_id"])
    op.create_table(
        "subject_incidents",
        sa.Column("incident_id", UUID, primary_key=True),
        sa.Column("subject_id", UUID, sa.ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("camera_id", sa.String(255), nullable=False),
        _ts("detected_at"),
        sa.Column("match_type", sa.String(16), nullable=False),
        sa.Column("similarity", sa.Float(), nullable=True),
        _ts("created_at", default=True),
    )
    op.create_index("ix_subject_incidents_subject_id", "subject_incidents", ["subject_id"])
    op.create_table(
        "face_incidents",
        sa.Column("incident_id", UUID, primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("camera_id", sa.String(255), nullable=False),
        _ts("detected_at"),
        _ts("created_at", default=True),
    )
    op.create_table(
        "subject_match_reviews",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("incident_id", UUID, nullable=False),
        sa.Column("new_subject_id", UUID, sa.ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("candidate_subject_id", UUID, sa.ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("similarity", sa.Float(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("decided_by", sa.String(255), nullable=True),
        _ts("decided_at", nullable=True),
        _ts("created_at", default=True),
    )
    op.create_index("ix_subject_match_reviews_new_subject_id", "subject_match_reviews", ["new_subject_id"])
    op.create_index("ix_subject_match_reviews_candidate_subject_id", "subject_match_reviews", ["candidate_subject_id"])
    op.create_table(
        "subject_access_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("subject_id", UUID, nullable=True),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("client_ip", sa.String(64), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
        _ts("at", default=True),
    )
    op.add_column("alerts", sa.Column("incident_id", UUID, nullable=True))
    op.create_index("ix_alerts_incident_id", "alerts", ["incident_id"])


def downgrade() -> None:
    op.drop_index("ix_alerts_incident_id", table_name="alerts")
    op.drop_column("alerts", "incident_id")
    op.drop_table("subject_access_log")
    op.drop_table("subject_match_reviews")
    op.drop_table("face_incidents")
    op.drop_table("subject_incidents")
    op.drop_table("subject_faces")
    op.drop_table("subjects")
    op.execute("DROP SEQUENCE IF EXISTS subject_code_seq")

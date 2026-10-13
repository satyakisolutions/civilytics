"""Soft-delete cameras: add cameras.deleted_at.

Cameras accrue alerts, violation events, plate reads and track sessions that
must survive the camera's removal (enforcement record / chain of custody).
The FKs on all four tables RESTRICT camera deletion, so once alerts began
persisting (they previously never reached the DB) DELETE /api/cameras 500'd
on any camera that had fired an alert. Instead of hard-deleting the row:

  * DELETE /api/cameras sets deleted_at=now() + status='offline'.
  * Every camera read filters out deleted_at IS NOT NULL, so the deleted
    camera gets a 404 and drops out of the dashboard, while its history
    remains queryable by camera_id.

Revision ID: 013
Revises: 012
"""

from alembic import op
import sqlalchemy as sa

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cameras",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cameras", "deleted_at")
"""Add snapshot_url to alerts

Revision ID: 007
Revises: 006
Create Date: 2026-09-02 00:00:00.000000

The `alerts` table (what HistoryView/MonitoringContext actually query via
GET /api/alerts) had no image field at all -- alerts fired via
kafka_producer.send_alert() (pipeline/app/pipeline.py) never had anywhere
to attach a frame, so "no frames for these evidences" was structural, not
a bug in any one code path. The separate pipeline/app/evidence_writer.py
JSON+keyframe system writes to disk under /app/evidence but was never
wired to any DB table this API/frontend actually reads.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("alerts", sa.Column("snapshot_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("alerts", "snapshot_url")

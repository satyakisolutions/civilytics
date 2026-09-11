"""Add per-camera detection_config for configurable dwell/proximity thresholds

Revision ID: 006
Revises: 005
Create Date: 2026-09-02 00:00:00.000000

Lets each camera override the pipeline's global waste-dump detection
thresholds (dwell_violation_threshold_s, waste_proximity_alert_window_s,
waste_proximity_px) instead of one fixed set of values for every camera --
different camera placements (narrow alley vs open lot) need different
dwell/proximity tuning to avoid false positives/negatives.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "cameras",
        sa.Column("detection_config", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cameras", "detection_config")

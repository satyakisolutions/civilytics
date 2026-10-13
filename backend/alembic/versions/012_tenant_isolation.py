"""Per-tenant data isolation (T1): tenant_id on the 6 remaining tenant-owned
tables. Camera and Zone already carry it (migration 009).

Nullable on purpose: worker-side inserts land tenant_id NULL until the worker
layer stamps them, and a NOT NULL here would reject those writes outright.

Revision ID: 012
Revises: 011
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None

_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"

# (table, FK constraint name, index name) for the 6 tables that still lack a
# tenant_id. Camera/Zone are intentionally absent -- 009 already did them.
_TABLES = [
    "violation_events",
    "alerts",
    "plate_reads",
    "evidence_files",
    "track_sessions",
    "log_entries",
]


def upgrade() -> None:
    # The default tenant row is created by migration 009; re-assert it so the
    # backfill FK never dangles if 009's row was removed out of band.
    op.execute(
        f"INSERT INTO tenants (id, name) "
        f"VALUES ('{_DEFAULT_TENANT}'::uuid, 'Default customer') "
        f"ON CONFLICT (id) DO NOTHING"
    )

    for table in _TABLES:
        op.add_column(table, sa.Column("tenant_id", UUID(as_uuid=True), nullable=True))
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
        op.create_foreign_key(
            f"fk_{table}_tenant_id", table, "tenants", ["tenant_id"], ["id"]
        )
        # Backfill every existing row to the bootstrap tenant.
        op.execute(f"UPDATE {table} SET tenant_id = '{_DEFAULT_TENANT}'::uuid")


def downgrade() -> None:
    for table in _TABLES:
        op.drop_constraint(f"fk_{table}_tenant_id", table, type_="foreignkey")
        op.drop_index(f"ix_{table}_tenant_id", table_name=table)
        op.drop_column(table, "tenant_id")

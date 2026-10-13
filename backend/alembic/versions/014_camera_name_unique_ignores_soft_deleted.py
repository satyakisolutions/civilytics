"""Camera name uniqueness ignores soft-deleted rows.

uq_cameras_tenant_name was a full UNIQUE(tenant_id, name) constraint, so a
soft-deleted camera (deleted_at IS NOT NULL, migration 013) kept holding its
name forever: deleting "Cam1" made it impossible to create a new "Cam1" (the
insert hit the constraint and 500'd/409'd). Replace it with a PARTIAL unique
index that only applies to live rows (deleted_at IS NULL), so a deleted
camera's name becomes reusable while two live cameras still can't collide.
"""
from alembic import op

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the full constraint, add a partial unique index of the same name.
    op.drop_constraint("uq_cameras_tenant_name", "cameras", type_="unique")
    op.create_index(
        "uq_cameras_tenant_name",
        "cameras",
        ["tenant_id", "name"],
        unique=True,
        postgresql_where="deleted_at IS NULL",
    )


def downgrade() -> None:
    op.drop_index("uq_cameras_tenant_name", table_name="cameras")
    op.create_unique_constraint(
        "uq_cameras_tenant_name", "cameras", ["tenant_id", "name"]
    )

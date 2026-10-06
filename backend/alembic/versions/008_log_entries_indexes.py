"""Indexes for log_entries, needed once browser console logs land here.

Browser sessions produce far more rows than services do -- an 8-hour shift
at a few lines a second is six figures per operator -- so the two queries
the log viewer actually runs need indexes or they become sequential scans
over the whole table.

Revision ID: 008
Revises: 007
"""
from alembic import op

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # "newest N rows for this service", the default log-viewer query.
    op.create_index(
        "ix_log_entries_service_timestamp",
        "log_entries",
        ["service", "timestamp"],
        postgresql_using="btree",
    )
    # "everything from one browser session", for following a bug report.
    # Expression index on the JSONB key, since session_id lives in metadata
    # rather than its own column -- log_entries already existed and adding a
    # column would mean rewriting the table.
    op.execute(
        "CREATE INDEX ix_log_entries_session_id "
        "ON log_entries ((metadata->>'session_id'))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_log_entries_session_id")
    op.drop_index("ix_log_entries_service_timestamp", table_name="log_entries")

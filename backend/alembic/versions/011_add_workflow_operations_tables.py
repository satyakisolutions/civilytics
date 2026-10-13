"""Add workflow operations tables: workflow_event_links, worker_heartbeats,
ops_alert_rules, ops_alerts, ops_alert_deliveries, workflow_action_audit.

Revision ID: 011
Revises: 010
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_event_links",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace_id", sa.String(255), nullable=False),
        sa.Column("event_id", sa.String(255), nullable=False),
        sa.Column("conductor_workflow_id", sa.String(255), nullable=True),
        sa.Column("workflow_name", sa.String(255), nullable=False),
        sa.Column("workflow_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("correlation_id", sa.String(255), nullable=True),
        sa.Column("kafka_topic", sa.String(255), nullable=True),
        sa.Column("kafka_partition", sa.Integer, nullable=True),
        sa.Column("kafka_offset", sa.BigInteger, nullable=True),
        sa.Column("workflow_status", sa.String(50), nullable=False, server_default="PENDING"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("event_id", "workflow_name", name="uq_event_workflow"),
    )
    op.create_index("ix_workflow_event_links_event_id", "workflow_event_links", ["event_id"])
    op.create_index("ix_workflow_event_links_conductor_wf_id", "workflow_event_links", ["conductor_workflow_id"])
    op.create_index("ix_workflow_event_links_status", "workflow_event_links", ["workflow_status"])

    op.create_table(
        "worker_heartbeats",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("worker_service", sa.String(255), nullable=False),
        sa.Column("task_type", sa.String(255), nullable=False),
        sa.Column("instance_id", sa.String(255), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="ACTIVE"),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_poll_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("build_version", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "ops_alert_rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace_id", sa.String(255), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("source_type", sa.String(100), nullable=False),
        sa.Column("condition_type", sa.String(100), nullable=False),
        sa.Column("threshold_value", sa.Float, nullable=True),
        sa.Column("window_minutes", sa.Integer, nullable=True),
        sa.Column("severity", sa.String(50), nullable=False, server_default="WARNING"),
        sa.Column("delivery_channels", JSONB, nullable=False, server_default="[]"),
        sa.Column("cooldown_minutes", sa.Integer, nullable=False, server_default="15"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    # Workflow-ops alerts are a SEPARATE subsystem from the event-based
    # alerts/alert_deliveries created in 001/003 (which the dashboard's
    # /api/alerts route + db.models.Alert still own). They live in their own
    # ops_* tables (matching db/workflow_models.py), so we must NOT touch the
    # legacy pair here — doing so breaks the camera-alert route.
    op.create_table(
        "ops_alerts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("rule_id", UUID(as_uuid=True), sa.ForeignKey("ops_alert_rules.id"), nullable=True),
        sa.Column("workspace_id", sa.String(255), nullable=True),
        sa.Column("severity", sa.String(50), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("source_type", sa.String(100), nullable=True),
        sa.Column("source_id", sa.String(255), nullable=True),
        sa.Column("dedupe_key", sa.String(500), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="OPEN"),
        sa.Column("first_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.String(255), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(255), nullable=True),
        sa.Column("resolution_note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_oa_dedupe", "ops_alerts", ["dedupe_key"])
    op.create_index("ix_oa_status", "ops_alerts", ["status"])
    op.create_index("ix_oa_severity", "ops_alerts", ["severity"])
    op.create_index("ix_oa_created", "ops_alerts", ["created_at"])

    op.create_table(
        "ops_alert_deliveries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("alert_id", UUID(as_uuid=True), sa.ForeignKey("ops_alerts.id"), nullable=False),
        sa.Column("channel", sa.String(50), nullable=False),
        sa.Column("recipient", sa.String(500), nullable=True),
        sa.Column("delivery_status", sa.String(50), nullable=False, server_default="PENDING"),
        sa.Column("provider_message_id", sa.String(255), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "workflow_action_audit",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("actor_id", sa.String(255), nullable=False),
        sa.Column("actor_role", sa.String(100), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("target_type", sa.String(100), nullable=False),
        sa.Column("target_id", sa.String(255), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("result", sa.String(100), nullable=True),
        sa.Column("request_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_wf_audit_created_at", "workflow_action_audit", ["created_at"])
    op.create_index("ix_wf_audit_target", "workflow_action_audit", ["target_type", "target_id"])


def downgrade() -> None:
    op.drop_table("ops_alert_deliveries")
    op.drop_table("ops_alerts")
    op.drop_table("ops_alert_rules")
    op.drop_table("workflow_action_audit")
    op.drop_table("worker_heartbeats")
    op.drop_table("workflow_event_links")

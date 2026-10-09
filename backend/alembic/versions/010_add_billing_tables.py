"""Add billing tables: subscriptions, invoices, billing_contacts, service_requests.

Revision ID: 010
Revises: 009
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TYPE subscriptionstatus AS ENUM ('active', 'paused', 'cancelled')")
    op.execute("CREATE TYPE billingcycle AS ENUM ('monthly', 'annual')")
    op.execute("CREATE TYPE paymentstatus AS ENUM ('due', 'paid', 'overdue', 'cancelled')")
    op.execute("CREATE TYPE servicerequesttype AS ENUM ('add_camera', 'change_plan', 'cancel')")
    op.execute("CREATE TYPE servicerequeststatus AS ENUM ('pending', 'approved', 'rejected')")

    op.create_table(
        "subscriptions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("plan_name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("status", sa.Enum("active", "paused", "cancelled", name="subscriptionstatus", create_type=False), nullable=False, server_default="active"),
        sa.Column("billing_cycle", sa.Enum("monthly", "annual", name="billingcycle", create_type=False), nullable=False, server_default="monthly"),
        sa.Column("start_date", sa.Date, nullable=False),
        sa.Column("next_renewal", sa.Date, nullable=False),
        sa.Column("monthly_amount_paise", sa.Integer, nullable=False),
        sa.Column("subscription_ext_id", sa.String(50), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_subscriptions_tenant_id", "subscriptions", ["tenant_id"])

    op.create_table(
        "invoices",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("subscription_id", UUID(as_uuid=True), sa.ForeignKey("subscriptions.id"), nullable=False),
        sa.Column("invoice_number", sa.String(50), nullable=False, unique=True),
        sa.Column("billing_period_start", sa.Date, nullable=False),
        sa.Column("billing_period_end", sa.Date, nullable=False),
        sa.Column("issued_date", sa.Date, nullable=False),
        sa.Column("due_date", sa.Date, nullable=False),
        sa.Column("amount_paise", sa.Integer, nullable=False),
        sa.Column("tax_paise", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_paise", sa.Integer, nullable=False),
        sa.Column("payment_status", sa.Enum("due", "paid", "overdue", "cancelled", name="paymentstatus", create_type=False), nullable=False, server_default="due"),
        sa.Column("razorpay_payment_id", sa.String(100), nullable=True),
        sa.Column("pdf_url", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_invoices_tenant_id", "invoices", ["tenant_id"])
    op.create_index("ix_invoices_subscription_id", "invoices", ["subscription_id"])

    op.create_table(
        "billing_contacts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False, unique=True),
        sa.Column("org_name", sa.String(255), nullable=False),
        sa.Column("contact_name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(30), nullable=True),
        sa.Column("address", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_billing_contacts_tenant_id", "billing_contacts", ["tenant_id"])

    op.create_table(
        "service_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("request_type", sa.Enum("add_camera", "change_plan", "cancel", name="servicerequesttype", create_type=False), nullable=False),
        sa.Column("workspace", sa.String(255), nullable=True),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("expected_date", sa.Date, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("status", sa.Enum("pending", "approved", "rejected", name="servicerequeststatus", create_type=False), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_service_requests_tenant_id", "service_requests", ["tenant_id"])

    # Seed demo data for the default tenant
    tenant_id = "00000000-0000-0000-0000-000000000001"
    op.execute(f"""
        INSERT INTO subscriptions (tenant_id, plan_name, description, status, billing_cycle, start_date, next_renewal, monthly_amount_paise, subscription_ext_id)
        VALUES (
            '{tenant_id}',
            'Civilytics Monitoring Plan',
            'Continuous video processing, event detection, evidence, and dashboard access.',
            'active', 'monthly',
            '2026-10-01', '2026-11-01',
            1175000,
            'CIV-BLR-2026-001'
        )
    """)

    op.execute(f"""
        INSERT INTO billing_contacts (tenant_id, org_name, contact_name, email, phone, address)
        VALUES (
            '{tenant_id}',
            'Let''s Be The Change',
            'Sandesh C',
            'accounts@letsbethechange.in',
            '+91 93521 8818',
            'Bengaluru, Karnataka, India'
        )
    """)

    op.execute(f"""
        INSERT INTO invoices (tenant_id, subscription_id, invoice_number, billing_period_start, billing_period_end, issued_date, due_date, amount_paise, tax_paise, total_paise, payment_status)
        SELECT '{tenant_id}', s.id, 'INV-CIV-2026-1001', '2026-10-01', '2026-10-31', '2026-10-01', '2026-10-15', 1175000, 211500, 1386500, 'due'
        FROM subscriptions s WHERE s.subscription_ext_id = 'CIV-BLR-2026-001'
    """)
    op.execute(f"""
        INSERT INTO invoices (tenant_id, subscription_id, invoice_number, billing_period_start, billing_period_end, issued_date, due_date, amount_paise, tax_paise, total_paise, payment_status)
        SELECT '{tenant_id}', s.id, 'INV-CIV-2026-0901', '2026-09-01', '2026-09-30', '2026-09-01', '2026-09-15', 1175000, 211500, 1386500, 'paid'
        FROM subscriptions s WHERE s.subscription_ext_id = 'CIV-BLR-2026-001'
    """)
    op.execute(f"""
        INSERT INTO invoices (tenant_id, subscription_id, invoice_number, billing_period_start, billing_period_end, issued_date, due_date, amount_paise, tax_paise, total_paise, payment_status)
        SELECT '{tenant_id}', s.id, 'INV-CIV-2026-0801', '2026-08-01', '2026-08-31', '2026-08-01', '2026-08-15', 1175000, 211500, 1386500, 'paid'
        FROM subscriptions s WHERE s.subscription_ext_id = 'CIV-BLR-2026-001'
    """)


def downgrade() -> None:
    op.drop_table("service_requests")
    op.drop_table("billing_contacts")
    op.drop_table("invoices")
    op.drop_table("subscriptions")
    op.execute("DROP TYPE IF EXISTS servicerequeststatus")
    op.execute("DROP TYPE IF EXISTS servicerequesttype")
    op.execute("DROP TYPE IF EXISTS paymentstatus")
    op.execute("DROP TYPE IF EXISTS billingcycle")
    op.execute("DROP TYPE IF EXISTS subscriptionstatus")

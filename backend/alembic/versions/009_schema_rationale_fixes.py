"""Target-schema fixes from docs/DB_SCHEMA_RATIONALE.md.

Tenancy, zones, scheduler cadence, acknowledgement attribution, derived
categories, and the constraints/indexes the schema was missing.

Safe to run here: at authoring time the only populated app tables were
`cameras` (3 rows) and `system_config` (12). Backfill below assumes nothing
beyond that and will still work with data, but review the zone backfill if
this is applied to a populated database -- it derives one zone row per
distinct `cameras.zone` string.

Revision ID: 009
Revises: 008
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None

# Every existing row belongs to one bootstrap customer. A fixed uuid rather
# than a generated one so this migration is idempotent across environments
# and so the value can be put into an owner's ww_tenant attribute by hand.
_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    bind = op.get_bind()

    # ── enum types ────────────────────────────────────────────────────────
    # The models declare create_type=False, so the types must exist first.
    for name, values in (
        ("delivery_status", ("pending", "sent", "failed")),
        ("sync_status", ("pending", "uploading", "synced", "failed")),
        ("remote_backend", ("gdrive", "dropbox", "s3")),
        ("ack_source", ("dashboard", "ivr", "api")),
    ):
        vals = ", ".join(f"'{v}'" for v in values)
        op.execute(f"DO $$ BEGIN CREATE TYPE {name} AS ENUM ({vals}); "
                   f"EXCEPTION WHEN duplicate_object THEN NULL; END $$;")

    # ── tenants ───────────────────────────────────────────────────────────
    op.create_table(
        "tenants",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("retention_days", sa.Integer, nullable=False, server_default="90"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.execute(
        f"INSERT INTO tenants (id, name) VALUES ('{_DEFAULT_TENANT}'::uuid, 'Default customer')"
    )

    # ── zones ─────────────────────────────────────────────────────────────
    op.create_table(
        "zones",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        # Default FALSE on purpose: a zone is not safe to broadcast until a
        # human says it is. Defaulting true would make the unsafe case the
        # one you get by doing nothing.
        sa.Column("is_speakable", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("eps_minutes", sa.Integer, nullable=False, server_default="45"),
        sa.Column("min_events", sa.Integer, nullable=False, server_default="6"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_unique_constraint("uq_zones_tenant_name", "zones", ["tenant_id", "name"])
    op.create_index("ix_zones_tenant_id", "zones", ["tenant_id"])

    # One zone per distinct existing camera zone string, under the bootstrap
    # tenant. is_speakable stays FALSE so nothing becomes broadcastable by
    # migration -- that is a human decision.
    op.execute(
        f"INSERT INTO zones (tenant_id, name) "
        f"SELECT DISTINCT '{_DEFAULT_TENANT}'::uuid, zone FROM cameras WHERE zone IS NOT NULL"
    )

    # ── scheduler_jobs ────────────────────────────────────────────────────
    op.create_table(
        "scheduler_jobs",
        sa.Column("job", sa.String(64), primary_key=True),
        sa.Column("interval_s", sa.Integer, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("updated_by", sa.String(255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    # Deliberately NOT seeded. The code registry owns the defaults; a row
    # here means "someone changed this", which is what updated_by records.

    # ── cameras ───────────────────────────────────────────────────────────
    op.add_column("cameras", sa.Column("tenant_id", UUID(as_uuid=True), nullable=True))
    op.add_column("cameras", sa.Column("zone_id", UUID(as_uuid=True), nullable=True))
    op.execute(f"UPDATE cameras SET tenant_id = '{_DEFAULT_TENANT}'::uuid")
    op.execute(
        "UPDATE cameras c SET zone_id = z.id "
        f"FROM zones z WHERE z.name = c.zone AND z.tenant_id = '{_DEFAULT_TENANT}'::uuid"
    )
    # Any camera with a NULL zone string gets a placeholder rather than
    # failing the NOT NULL -- losing a camera row to a migration is worse
    # than an obviously-named zone someone has to rename.
    op.execute(
        f"INSERT INTO zones (tenant_id, name) "
        f"SELECT '{_DEFAULT_TENANT}'::uuid, 'Unassigned' "
        f"WHERE EXISTS (SELECT 1 FROM cameras WHERE zone_id IS NULL) "
        f"AND NOT EXISTS (SELECT 1 FROM zones WHERE name = 'Unassigned' "
        f"AND tenant_id = '{_DEFAULT_TENANT}'::uuid)"
    )
    op.execute(
        "UPDATE cameras SET zone_id = (SELECT id FROM zones WHERE name = 'Unassigned' "
        f"AND tenant_id = '{_DEFAULT_TENANT}'::uuid) WHERE zone_id IS NULL"
    )
    op.alter_column("cameras", "tenant_id", nullable=False)
    op.alter_column("cameras", "zone_id", nullable=False)
    op.create_foreign_key("fk_cameras_tenant", "cameras", "tenants", ["tenant_id"], ["id"])
    op.create_foreign_key("fk_cameras_zone", "cameras", "zones", ["zone_id"], ["id"])
    op.drop_column("cameras", "zone")

    op.create_unique_constraint("uq_cameras_tenant_name", "cameras", ["tenant_id", "name"])
    op.create_check_constraint(
        "ck_cameras_lat", "cameras", "gps_lat IS NULL OR (gps_lat BETWEEN -90 AND 90)"
    )
    op.create_check_constraint(
        "ck_cameras_lng", "cameras", "gps_lng IS NULL OR (gps_lng BETWEEN -180 AND 180)"
    )
    # Half a coordinate renders at lng=0 (Gulf of Guinea) instead of being
    # flagged as missing.
    op.create_check_constraint(
        "ck_cameras_gps_pair", "cameras", "(gps_lat IS NULL) = (gps_lng IS NULL)"
    )
    op.create_index("ix_cameras_tenant_status", "cameras", ["tenant_id", "status"])
    op.create_index("ix_cameras_zone_id", "cameras", ["zone_id"])

    # ── violation_events ──────────────────────────────────────────────────
    op.alter_column("violation_events", "vlm_response", new_column_name="relation_result")
    op.add_column("violation_events", sa.Column("category", sa.String(40), nullable=True))
    op.add_column("violation_events", sa.Column("actor_class", sa.String(20), nullable=True))
    op.add_column(
        "violation_events", sa.Column("category_run_id", UUID(as_uuid=True), nullable=True)
    )
    op.create_index(
        "ix_violation_events_camera_timestamp", "violation_events", ["camera_id", "timestamp"]
    )
    op.create_index("ix_violation_events_timestamp", "violation_events", ["timestamp"])
    op.create_index(
        "ix_violation_events_unreviewed", "violation_events", ["timestamp"],
        postgresql_where=sa.text("is_valid IS NULL"),
    )

    # ── alerts ────────────────────────────────────────────────────────────
    op.add_column("alerts", sa.Column("zone_id", UUID(as_uuid=True), nullable=True))
    op.execute(
        "UPDATE alerts a SET zone_id = z.id "
        f"FROM zones z WHERE z.name = a.zone AND z.tenant_id = '{_DEFAULT_TENANT}'::uuid"
    )
    op.create_foreign_key("fk_alerts_zone", "alerts", "zones", ["zone_id"], ["id"])
    op.drop_column("alerts", "zone")

    # The gap this migration most needed to close: the UI acknowledges
    # ALERTS, and this table had no attribution columns at all.
    op.add_column("alerts", sa.Column("acknowledged_by", sa.String(255), nullable=True))
    op.add_column(
        "alerts", sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "alerts",
        sa.Column("ack_source", sa.Enum(name="ack_source", create_type=False), nullable=True),
    )
    op.create_index("ix_alerts_camera_id", "alerts", ["camera_id"])
    op.create_index("ix_alerts_event_id", "alerts", ["event_id"])
    op.create_index("ix_alerts_zone_id", "alerts", ["zone_id"])
    op.create_index(
        "ix_alerts_active", "alerts", ["timestamp"],
        postgresql_where=sa.text("status = 'active'::alertstatus"),
    )

    # ── alert_deliveries ──────────────────────────────────────────────────
    # The existing partial index predicates on (status)::text. Once status is
    # an enum that cast is STABLE, not IMMUTABLE (enum labels can be renamed),
    # so rebuilding the index during ALTER TYPE fails with "functions in
    # index predicate must be marked IMMUTABLE". Drop it, change the type,
    # recreate the predicate enum-natively.
    op.execute("DROP INDEX IF EXISTS ix_alert_deliveries_status_pending")
    op.execute(
        "ALTER TABLE alert_deliveries ALTER COLUMN status DROP DEFAULT, "
        "ALTER COLUMN status TYPE delivery_status USING status::delivery_status, "
        "ALTER COLUMN status SET DEFAULT 'pending'"
    )
    op.create_index(
        "ix_alert_deliveries_status_pending", "alert_deliveries", ["status"],
        postgresql_where=sa.text("status <> 'sent'::delivery_status"),
    )
    op.create_index("ix_alert_deliveries_alert_id", "alert_deliveries", ["alert_id"])

    # ── track_sessions ────────────────────────────────────────────────────
    # entered_at is in the key because nvtracker reuses ids ~2s after a track
    # dies (maxShadowTrackingAge: 50), so without it two different people
    # collide onto one session.
    op.create_unique_constraint(
        "uq_track_sessions_identity", "track_sessions", ["camera_id", "track_id", "entered_at"]
    )
    op.create_index("ix_track_sessions_camera_id", "track_sessions", ["camera_id"])

    # ── plate_reads ───────────────────────────────────────────────────────
    op.add_column("plate_reads", sa.Column("plate_normalised", sa.String(16), nullable=True))
    op.execute(
        "UPDATE plate_reads SET plate_normalised = "
        "UPPER(REGEXP_REPLACE(plate_number, '[^A-Za-z0-9]', '', 'g'))"
    )
    op.alter_column("plate_reads", "plate_normalised", nullable=False)
    op.create_index("ix_plate_reads_normalised", "plate_reads", ["plate_normalised"])
    op.create_index("ix_plate_reads_camera_id", "plate_reads", ["camera_id"])
    op.create_index("ix_plate_reads_track_session_id", "plate_reads", ["track_session_id"])

    # ── evidence_files ────────────────────────────────────────────────────
    op.alter_column("evidence_files", "dropbox_path", new_column_name="remote_path")
    op.add_column(
        "evidence_files",
        sa.Column("remote_backend", sa.Enum(name="remote_backend", create_type=False),
                  nullable=True),
    )
    # Existing rows synced to Dropbox keep their provenance.
    op.execute(
        "UPDATE evidence_files SET remote_backend = 'dropbox' WHERE remote_path IS NOT NULL"
    )
    op.add_column("evidence_files", sa.Column("checksum", sa.String(64), nullable=True))
    op.execute(
        "ALTER TABLE evidence_files ALTER COLUMN sync_status DROP DEFAULT, "
        "ALTER COLUMN sync_status TYPE sync_status USING sync_status::sync_status, "
        "ALTER COLUMN sync_status SET DEFAULT 'pending'"
    )
    op.create_index("ix_evidence_files_event_id", "evidence_files", ["event_id"])
    op.create_index(
        "ix_evidence_files_unsynced", "evidence_files", ["created_at"],
        postgresql_where=sa.text("sync_status <> 'synced'::sync_status"),
    )

    # ── drops ─────────────────────────────────────────────────────────────
    # vlm_analyses described a vision-language model that no longer exists.
    op.drop_table("vlm_analyses")
    # app_role duplicated a hierarchy Keycloak owns and enforces.
    op.execute("DROP TYPE IF EXISTS approle")


def downgrade() -> None:
    raise NotImplementedError(
        "009 is not reversible. It drops vlm_analyses and collapses the "
        "free-text zone columns into a zones table; reversing would "
        "fabricate the dropped rows. Restore from a backup instead."
    )

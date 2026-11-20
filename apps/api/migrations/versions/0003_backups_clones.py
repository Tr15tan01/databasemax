"""encrypted backups, restore verification and clones

Revision ID: 0003
Revises: 0002
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KINDS = "kind in ('create','delete','stop','start','rotate','retry')"
NEW_KINDS = "kind in ('create','delete','stop','start','rotate','retry','backup','clone','verify_backup')"

# plan id -> (entitlement changes, feature line inserted after the storage line)
PLAN_CHANGES = {
    "free": ({"backup_retention_days": 3, "manual_backups_per_day": 1}, "Daily encrypted backups (3-day retention)"),
    "developer": (
        {"backup_retention_days": 7, "manual_backups_per_day": 5},
        "Daily encrypted backups (7-day retention), restore to a new database",
    ),
    "pro": (
        {"backup_retention_days": 14, "manual_backups_per_day": 20},
        "Daily encrypted backups (14-day retention), restore to a new database",
    ),
}


def upgrade() -> None:
    op.drop_constraint(op.f("ck_provisioning_jobs_kind"), "provisioning_jobs", type_="check")
    op.create_check_constraint(op.f("ck_provisioning_jobs_kind"), "provisioning_jobs", NEW_KINDS)

    op.add_column("backups", sa.Column("table_count", sa.Integer()))
    op.add_column("backups", sa.Column("pg_version", sa.String(8)))
    op.add_column("backups", sa.Column("store", sa.String(16)))
    op.add_column("backups", sa.Column("duration_ms", sa.Integer()))
    op.add_column("backups", sa.Column("started_at", sa.DateTime(timezone=True)))
    op.add_column("backups", sa.Column("verification_status", sa.String(16)))
    op.add_column("backups", sa.Column("verification_error", sa.Text()))
    op.add_column("backups", sa.Column("requested_by", postgresql.UUID(as_uuid=True)))
    op.create_check_constraint(op.f("ck_backups_kind"), "backups", "kind in ('scheduled','manual','final','pre_clone')")
    op.create_check_constraint(
        op.f("ck_backups_verification_status"),
        "backups",
        "verification_status IS NULL OR verification_status in ('passed','failed')",
    )
    op.create_index("ix_backups_status_expires", "backups", ["status", "expires_at"])
    op.create_index("ix_backups_database_created", "backups", ["database_id", "created_at"])

    op.add_column(
        "databases",
        sa.Column(
            "source_database_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("databases.id", ondelete="SET NULL", name="fk_databases_source_database_id_databases"),
        ),
    )
    op.add_column(
        "databases",
        sa.Column(
            "restored_from_backup_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("backups.id", ondelete="SET NULL", name="fk_databases_restored_from_backup_id_backups"),
        ),
    )
    op.add_column("databases", sa.Column("data_restored_at", sa.DateTime(timezone=True)))

    for pid, (ent, feature) in PLAN_CHANGES.items():
        op.execute(
            sa.text(
                "UPDATE plans SET entitlements = entitlements || CAST(:ent AS jsonb), "
                "features = CASE WHEN features ? :feature THEN features "
                "ELSE jsonb_insert(features, '{3}', to_jsonb(CAST(:feature AS text))) END "
                "WHERE id = :pid"
            ).bindparams(ent=json.dumps(ent), feature=feature, pid=pid)
        )


def downgrade() -> None:
    for pid, (_ent, feature) in PLAN_CHANGES.items():
        op.execute(
            sa.text(
                "UPDATE plans SET entitlements = entitlements - 'manual_backups_per_day', "
                "features = features - :feature WHERE id = :pid"
            ).bindparams(feature=feature, pid=pid)
        )
    op.drop_column("databases", "data_restored_at")
    op.drop_column("databases", "restored_from_backup_id")
    op.drop_column("databases", "source_database_id")
    op.drop_index("ix_backups_database_created", table_name="backups")
    op.drop_index("ix_backups_status_expires", table_name="backups")
    op.drop_constraint(op.f("ck_backups_verification_status"), "backups", type_="check")
    op.drop_constraint(op.f("ck_backups_kind"), "backups", type_="check")
    for col in (
        "requested_by",
        "verification_error",
        "verification_status",
        "started_at",
        "duration_ms",
        "store",
        "pg_version",
        "table_count",
    ):
        op.drop_column("backups", col)
    op.execute("DELETE FROM provisioning_jobs WHERE kind IN ('backup','clone','verify_backup')")
    op.drop_constraint(op.f("ck_provisioning_jobs_kind"), "provisioning_jobs", type_="check")
    op.create_check_constraint(op.f("ck_provisioning_jobs_kind"), "provisioning_jobs", OLD_KINDS)

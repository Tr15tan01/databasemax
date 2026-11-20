"""multi-host: host registry fields, backup host, migrations between hosts

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_STATUSES = (
    "pending", "validating", "queued", "provisioning", "configuring", "testing", "ready", "failed",
    "stopping", "stopped", "deleting", "deleted",
)  # fmt: skip
NEW_STATUSES = (*OLD_STATUSES, "migrating")
OLD_KINDS = ("create", "delete", "stop", "start", "rotate", "retry", "backup", "clone", "verify_backup")
NEW_KINDS = (*OLD_KINDS, "migrate")


def _in(values: tuple[str, ...]) -> str:
    return "(" + ",".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.add_column("infrastructure_hosts", sa.Column("token_secret_reference", postgresql.UUID(as_uuid=True)))
    op.add_column(
        "infrastructure_hosts",
        sa.Column("labels", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column("infrastructure_hosts", sa.Column("status_reason", sa.String(300)))
    op.add_column("infrastructure_hosts", sa.Column("status_changed_at", sa.DateTime(timezone=True)))
    op.add_column("infrastructure_hosts", sa.Column("last_error", sa.String(300)))
    op.add_column(
        "backups",
        sa.Column(
            "host_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "infrastructure_hosts.id", ondelete="SET NULL", name="fk_backups_host_id_infrastructure_hosts"
            ),
        ),
    )
    op.add_column(
        "databases",
        sa.Column(
            "migration_target_host_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "infrastructure_hosts.id",
                ondelete="SET NULL",
                name="fk_databases_migration_target_host_id_infrastructure_hosts",
            ),
        ),
    )
    op.drop_constraint(op.f("ck_databases_status"), "databases", type_="check")
    op.create_check_constraint(op.f("ck_databases_status"), "databases", "status in " + _in(NEW_STATUSES))
    op.drop_constraint(op.f("ck_provisioning_jobs_kind"), "provisioning_jobs", type_="check")
    op.create_check_constraint(op.f("ck_provisioning_jobs_kind"), "provisioning_jobs", "kind in " + _in(NEW_KINDS))
    op.drop_constraint(op.f("ck_backups_kind"), "backups", type_="check")
    op.create_check_constraint(
        op.f("ck_backups_kind"), "backups", "kind in ('scheduled','manual','final','pre_clone','migration')"
    )
    # Existing backups were taken on the database's host.
    op.execute("UPDATE backups b SET host_id = d.host_id FROM databases d WHERE d.id = b.database_id")


def downgrade() -> None:
    op.execute("UPDATE databases SET status = 'ready' WHERE status = 'migrating'")
    op.execute("DELETE FROM provisioning_jobs WHERE kind = 'migrate'")
    op.drop_constraint(op.f("ck_provisioning_jobs_kind"), "provisioning_jobs", type_="check")
    op.create_check_constraint(op.f("ck_provisioning_jobs_kind"), "provisioning_jobs", "kind in " + _in(OLD_KINDS))
    op.drop_constraint(op.f("ck_databases_status"), "databases", type_="check")
    op.create_check_constraint(op.f("ck_databases_status"), "databases", "status in " + _in(OLD_STATUSES))
    op.execute("DELETE FROM backups WHERE kind = 'migration'")
    op.drop_constraint(op.f("ck_backups_kind"), "backups", type_="check")
    op.create_check_constraint(op.f("ck_backups_kind"), "backups", "kind in ('scheduled','manual','final','pre_clone')")
    op.drop_column("databases", "migration_target_host_id")
    op.drop_column("backups", "host_id")
    for c in ("last_error", "status_changed_at", "status_reason", "labels", "token_secret_reference"):
        op.drop_column("infrastructure_hosts", c)

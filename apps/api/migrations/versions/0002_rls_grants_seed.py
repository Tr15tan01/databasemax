"""row-level security, grants, plan seed

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# table -> tenant column
RLS_TABLES = {
    "projects": "owner_id",
    "databases": "owner_id",
    "database_credentials": "owner_id",
    "provisioning_jobs": "owner_id",
    "database_events": "owner_id",
    "backups": "owner_id",
    "api_keys": "user_id",
    "notifications": "user_id",
    "usage_records": "user_id",
    "audit_events": "tenant_user_id",
    "subscriptions": "user_id",
    "payments": "user_id",
    "idempotency_keys": "user_id",
}

PLANS = [
    (
        "free",
        "Free",
        "For learning and prototypes.",
        0,
        0,
        {
            "max_projects": 1,
            "max_databases": 1,
            "max_total_storage_mb": 500,
            "max_storage_per_db_mb": 500,
            "max_connections_per_db": 20,
            "compute_profiles": ["nano"],
            "backup_retention_days": 7,
            "credential_rotation": True,
            "api_access": True,
            "usage_alerts": "none",
            "support": "community",
            "monitoring": "basic",
            "max_api_keys": 2,
        },
        [
            "1 project",
            "1 PostgreSQL database",
            "500 MB storage",
            "Shared compute (0.25 vCPU, 256 MB)",
            "Up to 20 connections",
            "Basic monitoring",
            "Community support",
            "Standard API access",
            "No credit card required",
        ],
    ),
    (
        "developer",
        "Developer",
        "For independent developers and small applications.",
        900,
        1,
        {
            "max_projects": 5,
            "max_databases": 5,
            "max_total_storage_mb": 5120,
            "max_storage_per_db_mb": 5120,
            "max_connections_per_db": 50,
            "compute_profiles": ["nano", "small"],
            "backup_retention_days": 7,
            "credential_rotation": True,
            "api_access": True,
            "usage_alerts": "basic",
            "support": "email",
            "monitoring": "standard",
            "max_api_keys": 10,
        },
        [
            "5 projects",
            "5 PostgreSQL databases",
            "5 GB total storage",
            "Up to 0.5 vCPU / 512 MB per database",
            "Up to 50 connections per database",
            "Usage dashboard",
            "Email support",
            "API access",
            "Credential rotation",
            "Basic usage alerts",
        ],
    ),
    (
        "pro",
        "Pro",
        "For production projects and small teams.",
        2900,
        2,
        {
            "max_projects": 20,
            "max_databases": 20,
            "max_total_storage_mb": 25600,
            "max_storage_per_db_mb": 25600,
            "max_connections_per_db": 100,
            "compute_profiles": ["nano", "small", "medium"],
            "backup_retention_days": 14,
            "credential_rotation": True,
            "api_access": True,
            "usage_alerts": "advanced",
            "support": "priority",
            "monitoring": "detailed",
            "max_api_keys": 25,
        },
        [
            "20 projects",
            "20 PostgreSQL databases",
            "25 GB total storage",
            "Up to 1 vCPU / 1 GB per database",
            "Up to 100 connections per database",
            "Detailed monitoring",
            "Priority email support",
            "API access",
            "Advanced usage alerts",
        ],
    ),
]


def upgrade() -> None:
    for table, col in RLS_TABLES.items():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
              USING (current_setting('dmx.bypass_rls', true) = 'on'
                     OR {col} = NULLIF(current_setting('dmx.user_id', true), '')::uuid)
              WITH CHECK (current_setting('dmx.bypass_rls', true) = 'on'
                     OR {col} = NULLIF(current_setting('dmx.user_id', true), '')::uuid)
            """
        )

    # Runtime role: DML everywhere (via default privileges) but the audit trail is append-only.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dmx_app') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO dmx_app;
            REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM dmx_app;
            REVOKE ALL ON alembic_version FROM dmx_app;
          END IF;
        END $$;
        """
    )

    import json

    for pid, name, desc, price, order, ent, features in PLANS:
        op.execute(
            "INSERT INTO plans (id, name, description, price_cents, currency, entitlements, features, active, sort_order) "
            f"VALUES ('{pid}', '{name}', '{desc}', {price}, 'USD', '{json.dumps(ent)}'::jsonb, "
            f"'{json.dumps(features)}'::jsonb, true, {order}) ON CONFLICT (id) DO NOTHING"
        )


def downgrade() -> None:
    op.execute("DELETE FROM plans WHERE id IN ('free','developer','pro')")
    for table in RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

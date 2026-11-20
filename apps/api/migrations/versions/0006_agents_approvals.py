"""agent identities, approval requests, per-key request counters

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RLS = {"approval_requests": "owner_id", "api_key_request_counters": "user_id"}


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("kind", sa.String(16), nullable=False, server_default="standard"))
    op.add_column("api_keys", sa.Column("requires_approval", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("api_keys", sa.Column("description", sa.String(300)))
    op.create_check_constraint(op.f("ck_api_keys_kind"), "api_keys", "kind in ('standard','agent')")

    op.create_table(
        "approval_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("api_key_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("api_keys.id", ondelete="SET NULL")),
        sa.Column("action", sa.String(48), nullable=False),
        sa.Column("resource_type", sa.String(32), nullable=False),
        sa.Column("resource_id", sa.String(64), nullable=False),
        sa.Column("summary", sa.String(300), nullable=False),
        sa.Column("params", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("request_ip", postgresql.INET()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decided_by", postgresql.UUID(as_uuid=True)),
        sa.Column("decision_reason", sa.String(500)),
        sa.Column("result", postgresql.JSONB()),
        sa.CheckConstraint(
            "status in ('pending','approved','rejected','expired','executed','failed')",
            name=op.f("ck_approval_requests_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_requests")),
    )
    op.create_index("ix_approval_requests_owner_status", "approval_requests", ["owner_id", "status"])
    op.create_table(
        "api_key_request_counters",
        sa.Column("api_key_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("hour", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("count", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("api_key_id", "hour", name=op.f("pk_api_key_request_counters")),
    )
    op.create_index(op.f("ix_api_key_request_counters_user_id"), "api_key_request_counters", ["user_id"], unique=False)
    for table, col in RLS.items():
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
        op.execute(
            f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dmx_app') THEN "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO dmx_app; END IF; END $$;"
        )


def downgrade() -> None:
    op.drop_table("api_key_request_counters")
    op.drop_index("ix_approval_requests_owner_status", table_name="approval_requests")
    op.drop_table("approval_requests")
    op.drop_constraint(op.f("ck_api_keys_kind"), "api_keys", type_="check")
    for c in ("description", "requires_approval", "kind"):
        op.drop_column("api_keys", c)

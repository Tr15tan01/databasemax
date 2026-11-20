"""per-account allowance for on-demand / final / pre-copy backups

Revision ID: 0004
Revises: 0003
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ALLOWANCE_MB = {"free": 1000, "developer": 10240, "pro": 51200}  # 2x total storage


def upgrade() -> None:
    for pid, mb in ALLOWANCE_MB.items():
        op.execute(
            sa.text("UPDATE plans SET entitlements = entitlements || CAST(:e AS jsonb) WHERE id = :pid").bindparams(
                e=json.dumps({"user_backup_storage_mb": mb}), pid=pid
            )
        )


def downgrade() -> None:
    op.execute("UPDATE plans SET entitlements = entitlements - 'user_backup_storage_mb'")

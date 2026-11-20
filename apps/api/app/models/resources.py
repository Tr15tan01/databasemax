"""Tenant resources: projects, databases, credentials, provisioning, infrastructure, backups."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Timestamps, UUIDPk, utcnow

DATABASE_STATUSES = (
    "pending",
    "validating",
    "queued",
    "provisioning",
    "configuring",
    "testing",
    "ready",
    "failed",
    "stopping",
    "stopped",
    "deleting",
    "deleted",
    "migrating",
)


class Project(UUIDPk, Timestamps, Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint("environment in ('development','staging','production')", name="environment"),
        CheckConstraint("status in ('active','archived','deleted')", name="status"),
        Index(
            "uq_projects_owner_name_live",
            "owner_id",
            func.lower(text("name")),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    environment: Mapped[str] = mapped_column(String(20), nullable=False, default="development")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    archived_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    databases: Mapped[list[Database]] = relationship(back_populates="project")


class InfrastructureHost(UUIDPk, Base):
    __tablename__ = "infrastructure_hosts"
    __table_args__ = (CheckConstraint("status in ('active','draining','offline','retired')", name="status"),)

    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="systemd")
    region: Mapped[str] = mapped_column(String(64), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    capacity_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    last_heartbeat_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )
    # Multi-host: the host's provisioner token (encrypted in `secrets`), placement labels,
    # and operator notes. The bootstrap host may use the environment token instead.
    token_secret_reference: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    labels: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    status_reason: Mapped[str | None] = mapped_column(String(300))
    status_changed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(300))


class Database(UUIDPk, Timestamps, Base):
    __tablename__ = "databases"
    __table_args__ = (
        CheckConstraint(
            "status in (" + ",".join(f"'{s}'" for s in DATABASE_STATUSES) + ")",
            name="status",
        ),
        CheckConstraint("environment in ('development','staging','production')", name="environment"),
        Index(
            "uq_databases_project_name_live",
            "project_id",
            func.lower(text("name")),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_databases_owner_status", "owner_id", "status"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    environment: Mapped[str] = mapped_column(String(20), nullable=False, default="development")
    postgres_version: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    status_reason: Mapped[str | None] = mapped_column(String(300))
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="systemd")
    region: Mapped[str] = mapped_column(String(64), nullable=False)
    host_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("infrastructure_hosts.id", ondelete="SET NULL")
    )
    # Globally unique routing identity, e.g. "db-k3j9x2ab1c". Public host = <route_id>.<suffix>
    route_id: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    # Provisioner instance id (container/volume naming); never shown to customers.
    internal_database_name: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    # Customer-visible PostgreSQL database name inside their cluster.
    pg_database_name: Mapped[str] = mapped_column(String(63), nullable=False)
    compute_profile: Mapped[str] = mapped_column(String(32), nullable=False)
    allocated_storage_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    max_connections: Mapped[int] = mapped_column(Integer, nullable=False)
    internal_endpoint_host: Mapped[str | None] = mapped_column(String(255))
    internal_endpoint_port: Mapped[int | None] = mapped_column(Integer)
    credential_reference: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    storage_used_bytes: Mapped[int | None] = mapped_column(BigInteger)
    connections_current: Mapped[int | None] = mapped_column(Integer)
    storage_over_limit: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    suspended_by_admin: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    metrics_measured_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    ready_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # Clones / restores: the database this one was copied from, the backup used, and when the
    # data was restored (makes the restore step idempotent across job retries).
    source_database_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("databases.id", ondelete="SET NULL")
    )
    restored_from_backup_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("backups.id", ondelete="SET NULL", use_alter=True)
    )
    data_restored_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # Operator-initiated move to another host (see the `migrate` job).
    migration_target_host_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("infrastructure_hosts.id", ondelete="SET NULL")
    )

    project: Mapped[Project] = relationship(back_populates="databases", foreign_keys=[project_id])


class Secret(UUIDPk, Base):
    """Encrypted secret material (AES-256-GCM). Only `app.security.secrets` reads this table."""

    __tablename__ = "secrets"

    key_id: Mapped[str] = mapped_column(String(32), nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary(12), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )
    destroyed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class DatabaseCredential(UUIDPk, Base):
    __tablename__ = "database_credentials"
    __table_args__ = (
        Index(
            "uq_db_credential_active",
            "database_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL AND activated_at IS NOT NULL"),
        ),
    )

    database_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("databases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    username: Mapped[str] = mapped_column(String(63), nullable=False)
    secret_reference: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secrets.id", ondelete="SET NULL")
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )
    activated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    rotated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_revealed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class ProvisioningJob(UUIDPk, Base):
    __tablename__ = "provisioning_jobs"
    __table_args__ = (
        CheckConstraint(
            "kind in ('create','delete','stop','start','rotate','retry','backup','clone','verify_backup','migrate')",
            name="kind",
        ),
        CheckConstraint("status in ('queued','running','succeeded','failed','cancelled')", name="status"),
        Index("ix_jobs_claim", "status", "run_after"),
        Index(
            "uq_jobs_idempotency",
            "owner_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    database_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("databases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    step: Mapped[str | None] = mapped_column(String(32))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    run_after: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    safe_error_message: Mapped[str | None] = mapped_column(String(500))


class DatabaseEvent(UUIDPk, Base):
    """Every lifecycle transition and notable operation for a database."""

    __tablename__ = "database_events"
    __table_args__ = (Index("ix_database_events_db_created", "database_id", "created_at"),)

    database_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("databases.id", ondelete="CASCADE"), nullable=False
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str | None] = mapped_column(String(20))
    message: Mapped[str | None] = mapped_column(String(500))
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False, default="system")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )


class Backup(UUIDPk, Base):
    __tablename__ = "backups"
    __table_args__ = (
        CheckConstraint(
            "status in ('pending','running','completed','failed','verified','expired','deleted')", name="status"
        ),
        CheckConstraint("kind in ('scheduled','manual','final','pre_clone','migration')", name="kind"),
        CheckConstraint(
            "verification_status IS NULL OR verification_status in ('passed','failed')", name="verification_status"
        ),
        Index("ix_backups_status_expires", "status", "expires_at"),
        Index("ix_backups_database_created", "database_id", "created_at"),
    )

    database_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("databases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="scheduled")
    storage_reference: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    size: Mapped[int | None] = mapped_column(BigInteger)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    encryption_key_id: Mapped[str | None] = mapped_column(String(32))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    table_count: Mapped[int | None] = mapped_column(Integer)
    pg_version: Mapped[str | None] = mapped_column(String(8))
    store: Mapped[str | None] = mapped_column(String(16))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    verification_status: Mapped[str | None] = mapped_column(String(16))
    verification_error: Mapped[str | None] = mapped_column(Text)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # Host whose store holds the object (matters for per-host local stores).
    host_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("infrastructure_hosts.id", ondelete="SET NULL")
    )

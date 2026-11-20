"""Billing, usage, API keys, audit, notifications, idempotency, outbox."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamps, UUIDPk, utcnow


class Plan(Base):
    """Internal plan & entitlements. Paddle price ids are mapped separately via configuration."""

    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    entitlements: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    features: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Subscription(UUIDPk, Timestamps, Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        CheckConstraint(
            "status in ('active','trialing','past_due','paused','canceled','grace','suspended')", name="status"
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    plan: Mapped[str] = mapped_column(String(32), ForeignKey("plans.id"), nullable=False, default="free")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    paddle_customer_id: Mapped[str | None] = mapped_column(String(64), index=True)
    paddle_subscription_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    paddle_price_id: Mapped[str | None] = mapped_column(String(64))
    period_start: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    grace_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_event_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class BillingEvent(UUIDPk, Base):
    """Verified provider webhook events (idempotency by provider event id) and reconciliation log."""

    __tablename__ = "billing_events"
    __table_args__ = (UniqueConstraint("provider", "event_id", name="uq_billing_event"),)

    provider: Mapped[str] = mapped_column(String(16), nullable=False, default="paddle")
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    occurred_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )
    processed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="received")
    error: Mapped[str | None] = mapped_column(String(500))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class Payment(UUIDPk, Base):
    __tablename__ = "payments"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider_transaction_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    invoice_number: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(String(200))
    billed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )


class UsageRecord(UUIDPk, Base):
    """Time-series usage measurements.

    `source` distinguishes measured values ('measured') from derived/estimated ones ('estimated')
    and allocations ('allocated'). Raw samples are rolled up hourly/daily and pruned by retention.
    """

    __tablename__ = "usage_records"
    __table_args__ = (
        Index("ix_usage_user_metric_time", "user_id", "metric", "measured_at"),
        Index("ix_usage_db_metric_time", "database_id", "metric", "measured_at"),
        CheckConstraint("granularity in ('raw','hour','day')", name="granularity"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    database_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    metric: Mapped[str] = mapped_column(String(48), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="measured")
    granularity: Mapped[str] = mapped_column(String(8), nullable=False, default="raw")
    measured_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period: Mapped[str] = mapped_column(String(7), nullable=False)  # YYYY-MM


class ApiKey(UUIDPk, Base):
    """Programmatic credentials (a machine identity distinct from the human user).

    The full key is shown once; we store HMAC-SHA256(pepper, key). `prefix` is a non-secret
    identifier for display and lookup.
    """

    __tablename__ = "api_keys"
    __table_args__ = (CheckConstraint("kind in ('standard','agent')", name="kind"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    prefix: Mapped[str] = mapped_column(String(24), nullable=False, unique=True)
    key_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False, unique=True)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String(48)), nullable=False)
    project_ids: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(UUID(as_uuid=True)))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_ip: Mapped[str | None] = mapped_column(INET)
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # "agent": a machine identity for automation/AI agents. Agents always need a human's approval
    # for destructive operations; standard keys can opt in (`requires_approval`).
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="standard", server_default="standard")
    requires_approval: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    description: Mapped[str | None] = mapped_column(String(300))


class ApprovalRequest(UUIDPk, Base):
    """A destructive operation requested by an API key that must be approved by the account
    owner (dashboard session, recent re-authentication) before it runs."""

    __tablename__ = "approval_requests"
    __table_args__ = (
        CheckConstraint("status in ('pending','approved','rejected','expired','executed','failed')", name="status"),
        Index("ix_approval_requests_owner_status", "owner_id", "status"),
    )

    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("api_keys.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str] = mapped_column(String(300), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    request_ip: Mapped[str | None] = mapped_column(INET)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    decision_reason: Mapped[str | None] = mapped_column(String(500))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class ApiKeyRequestCounter(Base):
    """Hourly request counts per API key (agent activity), flushed from Redis by the worker."""

    __tablename__ = "api_key_request_counters"

    api_key_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    hour: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class AuditEvent(UUIDPk, Base):
    """Append-only audit trail. `tenant_user_id` is whose activity feed the event belongs to;
    `actor_*` identifies who performed it (user, api_key, admin, system)."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_tenant_time", "tenant_user_id", "created_at"),
        Index("ix_audit_action_time", "action", "created_at"),
        CheckConstraint("actor_type in ('user','api_key','admin','system')", name="actor_type"),
    )

    tenant_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_api_key_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(32))
    resource_id: Mapped[str | None] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, default="success")
    reason: Mapped[str | None] = mapped_column(String(500))
    request_id: Mapped[str | None] = mapped_column(String(64))
    ip: Mapped[str | None] = mapped_column(INET)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )


class Notification(UUIDPk, Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_time", "user_id", "created_at"),
        Index(
            "uq_notifications_dedupe",
            "user_id",
            "dedupe_key",
            unique=True,
            postgresql_where=text("dedupe_key IS NOT NULL"),
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(48), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="info")
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    link: Mapped[str | None] = mapped_column(String(300))
    dedupe_key: Mapped[str | None] = mapped_column(String(160))
    read_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )


class IdempotencyKey(UUIDPk, Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("user_id", "key", "endpoint", name="uq_idempotency"),)

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False, index=True
    )


class EmailOutbox(UUIDPk, Base):
    """Transactional outbox for email; delivered by the worker with retries and dedupe."""

    __tablename__ = "email_outbox"
    __table_args__ = (
        Index("ix_email_outbox_pending", "status", "next_attempt_at"),
        Index("uq_email_outbox_dedupe", "dedupe_key", unique=True, postgresql_where=text("dedupe_key IS NOT NULL")),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    to_address: Mapped[str] = mapped_column(String(320), nullable=False)
    template: Mapped[str] = mapped_column(String(48), nullable=False)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    # Encrypted body reference: bodies can contain one-time links, so they're stored encrypted.
    body_secret_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    dedupe_key: Mapped[str | None] = mapped_column(String(160))
    last_error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"

    worker_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="worker")
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    info: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class ApiRequestCounter(Base):
    """Hourly API request counts per user (flushed from Redis by the worker)."""

    __tablename__ = "api_request_counters"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    hour: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


__all__ = [
    "ApiKey",
    "ApiKeyRequestCounter",
    "ApiRequestCounter",
    "ApprovalRequest",
    "AuditEvent",
    "BillingEvent",
    "EmailOutbox",
    "IdempotencyKey",
    "Notification",
    "Payment",
    "Plan",
    "Subscription",
    "UsageRecord",
    "WorkerHeartbeat",
]

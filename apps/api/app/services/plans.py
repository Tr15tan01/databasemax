"""Plan entitlements and server-side quota enforcement."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ApiError, QuotaExceeded
from app.models import Database, Plan, Project, ProvisioningJob, Subscription, utcnow

# Compute profiles offered to customers (the provisioner holds the authoritative limits).
PROFILES: dict[str, dict[str, Any]] = {
    "nano": {"label": "Nano", "cpus": 0.25, "memory_mb": 256},
    "small": {"label": "Small", "cpus": 0.5, "memory_mb": 512},
    "medium": {"label": "Medium", "cpus": 1.0, "memory_mb": 1024},
}

# Statuses that mean the subscription's paid entitlements apply.
PAID_OK = {"active", "trialing", "past_due", "grace"}


@dataclass
class Entitlements:
    plan_id: str
    plan_name: str
    status: str
    data: dict[str, Any]

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


def get_subscription(db: Session, user_id: uuid.UUID, *, lock: bool = False) -> Subscription:
    stmt = select(Subscription).where(Subscription.user_id == user_id)
    if lock:
        stmt = stmt.with_for_update()
    sub = db.scalar(stmt)
    if sub is None:
        sub = Subscription(id=uuid.uuid4(), user_id=user_id, plan="free", status="active")
        db.add(sub)
        db.flush()
        if lock:
            sub = db.scalar(select(Subscription).where(Subscription.user_id == user_id).with_for_update())
            assert sub is not None
    return sub


def effective_entitlements(db: Session, sub: Subscription) -> Entitlements:
    plan_id = sub.plan
    status = sub.status
    if plan_id != "free":
        in_grace = sub.grace_until is not None and sub.grace_until > utcnow()
        if status not in PAID_OK or (status in ("grace", "past_due") and not in_grace):
            plan_id = "free"
    plan = db.get(Plan, plan_id) or db.get(Plan, "free")
    assert plan is not None
    return Entitlements(plan_id=plan.id, plan_name=plan.name, status=status, data=dict(plan.entitlements))


def entitlements_for(db: Session, user_id: uuid.UUID) -> Entitlements:
    return effective_entitlements(db, get_subscription(db, user_id))


def usage_counts(db: Session, user_id: uuid.UUID) -> dict[str, int]:
    projects = (
        db.scalar(select(func.count(Project.id)).where(Project.owner_id == user_id, Project.deleted_at.is_(None))) or 0
    )
    dbs, alloc = db.execute(
        select(func.count(Database.id), func.coalesce(func.sum(Database.allocated_storage_mb), 0)).where(
            Database.owner_id == user_id, Database.status != "deleted"
        )
    ).one()
    used = (
        db.scalar(
            select(func.coalesce(func.sum(Database.storage_used_bytes), 0)).where(
                Database.owner_id == user_id, Database.status != "deleted"
            )
        )
        or 0
    )
    return {
        "projects": int(projects),
        "databases": int(dbs),
        "allocated_storage_mb": int(alloc),
        "storage_used_bytes": int(used),
    }


def check_can_create_project(db: Session, user_id: uuid.UUID) -> None:
    sub = get_subscription(db, user_id, lock=True)
    ent = effective_entitlements(db, sub)
    counts = usage_counts(db, user_id)
    if counts["projects"] >= ent["max_projects"]:
        raise QuotaExceeded(
            f"Your {ent.plan_name} plan allows {ent['max_projects']} project(s). Upgrade to create more.",
            details={"limit": ent["max_projects"], "used": counts["projects"], "resource": "projects"},
        )


def check_can_create_database(db: Session, user_id: uuid.UUID, *, storage_mb: int, profile: str) -> Entitlements:
    sub = get_subscription(db, user_id, lock=True)
    ent = effective_entitlements(db, sub)
    if sub.status in ("suspended",):
        raise ApiError(
            "Your account's billing is suspended. Update your payment method.",
            code="billing_suspended",
            status_code=402,
        )
    counts = usage_counts(db, user_id)
    if counts["databases"] >= ent["max_databases"]:
        raise QuotaExceeded(
            f"Your {ent.plan_name} plan allows {ent['max_databases']} database(s). Upgrade to create more.",
            details={"limit": ent["max_databases"], "used": counts["databases"], "resource": "databases"},
        )
    if profile not in ent["compute_profiles"]:
        raise QuotaExceeded(
            f"The '{profile}' size isn't available on the {ent.plan_name} plan.",
            details={"allowed": ent["compute_profiles"], "resource": "compute_profile"},
        )
    if storage_mb > ent["max_storage_per_db_mb"]:
        raise QuotaExceeded(
            f"Storage per database is limited to {ent['max_storage_per_db_mb']} MB on the {ent.plan_name} plan.",
            details={"limit": ent["max_storage_per_db_mb"], "resource": "storage_per_db"},
        )
    if counts["allocated_storage_mb"] + storage_mb > ent["max_total_storage_mb"]:
        remaining = ent["max_total_storage_mb"] - counts["allocated_storage_mb"]
        raise QuotaExceeded(
            f"Not enough storage left on your plan ({max(remaining, 0)} MB remaining).",
            details={
                "limit": ent["max_total_storage_mb"],
                "allocated": counts["allocated_storage_mb"],
                "resource": "storage",
            },
        )
    inflight = (
        db.scalar(
            select(func.count(ProvisioningJob.id)).where(
                ProvisioningJob.owner_id == user_id,
                ProvisioningJob.status.in_(("queued", "running")),
                # system-initiated maintenance does not count against the user's operations
                ProvisioningJob.kind.notin_(("backup", "verify_backup")),
            )
        )
        or 0
    )
    if inflight >= get_settings().max_concurrent_jobs_per_user:
        raise ApiError(
            "Too many operations in progress. Wait for them to finish and try again.",
            code="too_many_operations",
            status_code=429,
        )
    return ent


def default_storage_mb(ent: Entitlements, db: Session, user_id: uuid.UUID) -> int:
    counts = usage_counts(db, user_id)
    remaining = ent["max_total_storage_mb"] - counts["allocated_storage_mb"]
    return max(0, min(remaining, ent["max_storage_per_db_mb"], 1024 if ent["max_storage_per_db_mb"] > 1024 else 10**9))

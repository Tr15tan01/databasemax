"""Operator/admin API. Access requires an explicit admin role assignment (audited, CLI-granted).
Sensitive actions require recent re-authentication and a reason; every action is audited with
admin identity, action, target, timestamp, reason and outcome. Customer passwords are never
exposed here."""

from __future__ import annotations

import contextlib
import datetime as dt
import uuid
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, Query
from pydantic import Field
from sqlalchemy import Date, String, cast, func, or_, select, text
from sqlalchemy.orm import Session

from app.api.v1.activity import to_out as audit_out
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import Conflict, NotFound
from app.core.redis import wake_workers
from app.models import (
    AdminNote,
    AdminRoleAssignment,
    ApiKey,
    ApiRequestCounter,
    AuditEvent,
    Backup,
    BillingEvent,
    Database,
    DatabaseEvent,
    InfrastructureHost,
    LoginAttempt,
    Payment,
    Plan,
    Project,
    ProvisioningJob,
    Subscription,
    User,
    WorkerHeartbeat,
    utcnow,
)
from app.models import (
    Session as UserSession,
)
from app.monitoring import security as security_monitor
from app.provisioning import state_machine as sm
from app.provisioning.client import ProvisionerClient, ProvisionerError
from app.schemas.common import ApiModel, Message
from app.security.deps import Principal, require_admin, require_recent_auth
from app.security.sessions import revoke_all_sessions
from app.services import backup_service, database_service, plans
from app.services.audit import audit
from app.services.notifications import notify

router = APIRouter(prefix="/admin", tags=["admin"])


class ReasonIn(ApiModel):
    reason: str = Field(min_length=3, max_length=500)


class NoteIn(ApiModel):
    body: str = Field(min_length=1, max_length=4000)


def _series(db: Session, col: Any, where: list[Any], days: int) -> list[dict[str, Any]]:
    since = utcnow() - dt.timedelta(days=days)
    rows = dict(db.execute(select(cast(col, Date), func.count()).where(col >= since, *where).group_by(text("1"))).all())
    out = []
    for i in range(days, -1, -1):
        day = (utcnow() - dt.timedelta(days=i)).date()
        out.append({"date": day.isoformat(), "value": int(rows.get(day, 0))})
    return out


@router.get("/overview", summary="Platform KPIs")
def overview(
    db: Session = Depends(get_db, scope="function"), admin: Principal = Depends(require_admin)
) -> dict[str, Any]:
    now = utcnow()
    d1, d7, d30 = now - dt.timedelta(days=1), now - dt.timedelta(days=7), now - dt.timedelta(days=30)
    users_total = db.scalar(select(func.count(User.id)).where(User.deleted_at.is_(None))) or 0
    users_verified = (
        db.scalar(select(func.count(User.id)).where(User.deleted_at.is_(None), User.email_verified_at.is_not(None)))
        or 0
    )
    active_30 = (
        db.scalar(select(func.count(func.distinct(UserSession.user_id))).where(UserSession.last_seen_at >= d30)) or 0
    )
    active_1 = (
        db.scalar(select(func.count(func.distinct(UserSession.user_id))).where(UserSession.last_seen_at >= d1)) or 0
    )
    new_7 = db.scalar(select(func.count(User.id)).where(User.created_at >= d7)) or 0
    new_30 = db.scalar(select(func.count(User.id)).where(User.created_at >= d30)) or 0
    suspended = db.scalar(select(func.count(User.id)).where(User.status == "suspended")) or 0
    projects = db.scalar(select(func.count(Project.id)).where(Project.deleted_at.is_(None))) or 0
    by_status = dict(
        db.execute(
            select(Database.status, func.count()).where(Database.status != "deleted").group_by(Database.status)
        ).all()
    )
    storage_alloc, storage_used = db.execute(
        select(
            func.coalesce(func.sum(Database.allocated_storage_mb), 0),
            func.coalesce(func.sum(Database.storage_used_bytes), 0),
        ).where(Database.status != "deleted")
    ).one()
    queue = dict(
        db.execute(
            select(ProvisioningJob.status, func.count())
            .where(ProvisioningJob.status.in_(("queued", "running")))
            .group_by(ProvisioningJob.status)
        ).all()
    )
    jobs_30 = db.execute(
        select(ProvisioningJob.status, func.count())
        .where(
            ProvisioningJob.kind == "create",
            ProvisioningJob.created_at >= d30,
            ProvisioningJob.status.in_(("succeeded", "failed")),
        )
        .group_by(ProvisioningJob.status)
    ).all()
    j = dict(jobs_30)
    success_rate = (
        round(j.get("succeeded", 0) / (j.get("succeeded", 0) + j.get("failed", 0)) * 100, 1)
        if (j.get("succeeded", 0) + j.get("failed", 0))
        else None
    )
    durations = db.execute(
        select(
            func.percentile_cont(0.5).within_group(
                func.extract("epoch", ProvisioningJob.completed_at - ProvisioningJob.created_at)
            ),
            func.percentile_cont(0.95).within_group(
                func.extract("epoch", ProvisioningJob.completed_at - ProvisioningJob.created_at)
            ),
        ).where(
            ProvisioningJob.kind == "create", ProvisioningJob.status == "succeeded", ProvisioningJob.created_at >= d30
        )
    ).one()
    subs = db.execute(
        select(Subscription.plan, Subscription.status, func.count()).group_by(Subscription.plan, Subscription.status)
    ).all()
    prices = {p.id: p.price_cents for p in db.scalars(select(Plan))}
    mrr = sum(
        prices.get(plan, 0) * n
        for plan, status, n in subs
        if status in ("active", "past_due", "grace") and plan != "free"
    )
    paid_active = sum(n for plan, status, n in subs if plan != "free" and status in ("active", "trialing"))
    pay_fail_30 = (
        db.scalar(select(func.count(Payment.id)).where(Payment.status == "failed", Payment.created_at >= d30)) or 0
    )
    failed_logins_24 = (
        db.scalar(
            select(func.count(LoginAttempt.id)).where(LoginAttempt.success.is_(False), LoginAttempt.created_at >= d1)
        )
        or 0
    )
    throttled_24 = (
        db.scalar(
            select(func.count(LoginAttempt.id)).where(LoginAttempt.reason == "throttled", LoginAttempt.created_at >= d1)
        )
        or 0
    )
    api_24 = (
        db.scalar(select(func.coalesce(func.sum(ApiRequestCounter.count), 0)).where(ApiRequestCounter.hour >= d1)) or 0
    )
    api_7 = (
        db.scalar(select(func.coalesce(func.sum(ApiRequestCounter.count), 0)).where(ApiRequestCounter.hour >= d7)) or 0
    )
    hosts = db.scalars(select(InfrastructureHost)).all()
    worker_last = db.scalar(select(func.max(WorkerHeartbeat.last_seen_at)))
    api_series = db.execute(
        select(cast(ApiRequestCounter.hour, Date), func.sum(ApiRequestCounter.count))
        .where(ApiRequestCounter.hour >= now - dt.timedelta(days=14))
        .group_by(text("1"))
        .order_by(text("1"))
    ).all()
    return {
        "users": {
            "total": users_total,
            "verified": users_verified,
            "active_24h": active_1,
            "active_30d": active_30,
            "new_7d": new_7,
            "new_30d": new_30,
            "suspended": suspended,
        },
        "projects": projects,
        "databases": {
            "total": sum(by_status.values()),
            "by_status": by_status,
            "running": by_status.get("ready", 0),
            "failed": by_status.get("failed", 0),
        },
        "storage": {"allocated_mb": int(storage_alloc or 0), "used_bytes": int(storage_used or 0)},
        "provisioning": {
            "queued": queue.get("queued", 0),
            "running": queue.get("running", 0),
            "success_rate_30d": success_rate,
            "succeeded_30d": j.get("succeeded", 0),
            "failed_30d": j.get("failed", 0),
            "p50_seconds": round(durations[0], 1) if durations[0] is not None else None,
            "p95_seconds": round(durations[1], 1) if durations[1] is not None else None,
        },
        "billing": {
            "mrr_cents": mrr,
            "paid_subscriptions": paid_active,
            "payment_failures_30d": pay_fail_30,
            "by_plan": [{"plan": p, "status": s, "count": n} for p, s, n in subs],
            "mrr_note": "Sum of list prices of paid subscriptions in good standing; excludes taxes, "
            "discounts and currency conversion.",
        },
        "security": {"failed_logins_24h": failed_logins_24, "throttled_24h": throttled_24},
        "api": {
            "requests_24h": int(api_24),
            "requests_7d": int(api_7),
            "series_14d": [{"date": r[0].isoformat(), "value": int(r[1])} for r in api_series],
        },
        "infrastructure": {
            "hosts": [{"name": h.name, "status": h.status, "last_heartbeat_at": h.last_heartbeat_at} for h in hosts],
            "worker_last_seen_at": worker_last,
            "worker_healthy": bool(worker_last and now - worker_last < dt.timedelta(minutes=2)),
        },
        "backups": backup_service.admin_overview(db),
        "series": {
            "registrations_30d": _series(db, User.created_at, [], 30),
            "databases_created_30d": _series(db, Database.created_at, [], 30),
            "failed_jobs_30d": _series(db, ProvisioningJob.created_at, [ProvisioningJob.status == "failed"], 30),
        },
        "generated_at": now,
    }


# --- users ------------------------------------------------------------------------------------------


@router.get("/users", summary="Search users")
def list_users(
    db: Session = Depends(get_db, scope="function"),
    admin: Principal = Depends(require_admin),
    q: str | None = Query(None, max_length=320),
    status: str | None = Query(None, max_length=16),
    plan: str | None = Query(None, max_length=32),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
) -> dict[str, Any]:
    dbc = (
        select(
            Database.owner_id,
            func.count(Database.id).label("n"),
            func.coalesce(func.sum(Database.storage_used_bytes), 0).label("used"),
        )
        .where(Database.status != "deleted")
        .group_by(Database.owner_id)
        .subquery()
    )
    stmt = (
        select(User, Subscription.plan, Subscription.status, dbc.c.n, dbc.c.used)
        .join(Subscription, Subscription.user_id == User.id, isouter=True)
        .join(dbc, dbc.c.owner_id == User.id, isouter=True)
        .where(User.deleted_at.is_(None))
    )
    if q:
        try:
            stmt = stmt.where(or_(User.email.ilike(f"%{q}%"), User.id == uuid.UUID(q)))
        except ValueError:
            stmt = stmt.where(or_(User.email.ilike(f"%{q}%"), User.display_name.ilike(f"%{q}%")))
    if status:
        stmt = stmt.where(User.status == status)
    if plan:
        stmt = stmt.where(Subscription.plan == plan)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    admins = set(db.scalars(select(AdminRoleAssignment.user_id).where(AdminRoleAssignment.revoked_at.is_(None))))
    return {
        "items": [
            {
                "id": u.id,
                "email": u.email,
                "display_name": u.display_name,
                "status": u.status,
                "verified": u.is_verified,
                "created_at": u.created_at,
                "last_login_at": u.last_login_at,
                "plan": p or "free",
                "subscription_status": ss,
                "databases": int(n or 0),
                "storage_used_bytes": int(used or 0),
                "is_admin": u.id in admins,
            }
            for u, p, ss, n, used in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _user(db: Session, user_id: uuid.UUID) -> User:
    u = db.get(User, user_id)
    if u is None or u.deleted_at is not None:
        raise NotFound("User not found.")
    return u


@router.get("/users/{user_id}", summary="User details")
def get_user(
    user_id: uuid.UUID, db: Session = Depends(get_db, scope="function"), admin: Principal = Depends(require_admin)
) -> dict[str, Any]:
    u = _user(db, user_id)
    sub = plans.get_subscription(db, u.id)
    ent = plans.effective_entitlements(db, sub)
    projects = db.scalars(select(Project).where(Project.owner_id == u.id, Project.deleted_at.is_(None))).all()
    dbs = db.scalars(select(Database).where(Database.owner_id == u.id, Database.status != "deleted")).all()
    sessions = (
        db.scalar(
            select(func.count(UserSession.id)).where(
                UserSession.user_id == u.id, UserSession.revoked_at.is_(None), UserSession.expires_at > utcnow()
            )
        )
        or 0
    )
    keys = db.scalar(select(func.count(ApiKey.id)).where(ApiKey.user_id == u.id, ApiKey.revoked_at.is_(None))) or 0
    notes = db.execute(
        select(AdminNote, User.email)
        .join(User, User.id == AdminNote.author_id, isouter=True)
        .where(AdminNote.subject_user_id == u.id)
        .order_by(AdminNote.created_at.desc())
    ).all()
    logins = db.scalars(
        select(LoginAttempt).where(LoginAttempt.user_id == u.id).order_by(LoginAttempt.created_at.desc()).limit(20)
    ).all()
    events = db.scalars(
        select(AuditEvent).where(AuditEvent.tenant_user_id == u.id).order_by(AuditEvent.created_at.desc()).limit(50)
    ).all()
    return {
        "user": {
            "id": u.id,
            "email": u.email,
            "display_name": u.display_name,
            "status": u.status,
            "suspended_reason": u.suspended_reason,
            "verified": u.is_verified,
            "email_verified_at": u.email_verified_at,
            "created_at": u.created_at,
            "last_login_at": u.last_login_at,
            "has_password": bool(u.password_hash),
            "providers": [o.provider for o in u.oauth_accounts],
        },
        "subscription": {
            "plan": sub.plan,
            "effective_plan": ent.plan_id,
            "status": sub.status,
            "period_end": sub.period_end,
            "paddle_customer_id": sub.paddle_customer_id,
            "cancel_at_period_end": sub.cancel_at_period_end,
            "grace_until": sub.grace_until,
        },
        "usage": plans.usage_counts(db, u.id),
        "limits": ent.data,
        "active_sessions": sessions,
        "active_api_keys": keys,
        "projects": [
            {"id": p.id, "name": p.name, "environment": p.environment, "status": p.status, "created_at": p.created_at}
            for p in projects
        ],
        "databases": [database_service.to_out(db, d, with_job=False).model_dump() for d in dbs],
        "notes": [{"id": n.id, "body": n.body, "author": email, "created_at": n.created_at} for n, email in notes],
        "logins": [
            {
                "success": la.success,
                "reason": la.reason,
                "method": la.method,
                "ip": str(la.ip) if la.ip else None,
                "created_at": la.created_at,
            }
            for la in logins
        ],
        "audit": [audit_out(e) for e in events],
    }


@router.post("/users/{user_id}/suspend", response_model=Message, summary="Suspend an account")
def suspend_user(
    user_id: uuid.UUID,
    body: ReasonIn,
    db: Session = Depends(get_db, scope="function"),
    admin: Principal = Depends(require_admin),
) -> Message:
    require_recent_auth(admin)
    u = _user(db, user_id)
    if u.id == admin.user.id:
        raise Conflict("You can't suspend your own account.", code="invalid_target")
    if u.status == "suspended":
        return Message(status="unchanged")
    u.status = "suspended"
    u.suspended_reason = body.reason
    n = revoke_all_sessions(db, u.id, "admin_suspended")
    audit(
        db,
        "admin.user_suspend",
        principal=admin,
        actor_type="admin",
        tenant_user_id=u.id,
        resource_type="user",
        resource_id=u.id,
        reason=body.reason,
        metadata={"sessions_revoked": n},
    )
    notify(
        db,
        u.id,
        "admin_action",
        "Your account was suspended",
        "Your databases stop accepting connections while the account is suspended. Contact support for details.",
        severity="error",
        dedupe_key=f"suspend:{u.id}:{utcnow():%Y%m%d%H%M}",
    )
    return Message(status="suspended")


@router.post("/users/{user_id}/reactivate", response_model=Message, summary="Reactivate an account")
def reactivate_user(
    user_id: uuid.UUID,
    body: ReasonIn,
    db: Session = Depends(get_db, scope="function"),
    admin: Principal = Depends(require_admin),
) -> Message:
    require_recent_auth(admin)
    u = _user(db, user_id)
    if u.status != "suspended":
        return Message(status="unchanged")
    u.status = "active"
    u.suspended_reason = None
    audit(
        db,
        "admin.user_reactivate",
        principal=admin,
        actor_type="admin",
        tenant_user_id=u.id,
        resource_type="user",
        resource_id=u.id,
        reason=body.reason,
    )
    notify(
        db,
        u.id,
        "admin_action",
        "Your account was reactivated",
        "Your databases accept connections again.",
        dedupe_key=f"reactivate:{u.id}:{utcnow():%Y%m%d%H%M}",
    )
    return Message(status="active")


@router.post("/users/{user_id}/verify-email", response_model=Message, summary="Mark an account's email as verified")
def admin_verify_email(
    user_id: uuid.UUID,
    body: ReasonIn,
    db: Session = Depends(get_db, scope="function"),
    admin: Principal = Depends(require_admin),
) -> Message:
    from app.services.auth_service import mark_email_verified

    require_recent_auth(admin)
    u = _user(db, user_id)
    if not mark_email_verified(db, u):
        return Message(status="unchanged", message="Already verified.")
    audit(
        db,
        "admin.user_email_verified",
        principal=admin,
        actor_type="admin",
        tenant_user_id=u.id,
        resource_type="user",
        resource_id=u.id,
        reason=body.reason,
    )
    return Message(status="verified")


@router.post("/users/{user_id}/revoke-sessions", response_model=Message, summary="Sign a user out everywhere")
def revoke_user_sessions(
    user_id: uuid.UUID,
    body: ReasonIn,
    db: Session = Depends(get_db, scope="function"),
    admin: Principal = Depends(require_admin),
) -> Message:
    require_recent_auth(admin)
    u = _user(db, user_id)
    n = revoke_all_sessions(db, u.id, "admin_revoked")
    audit(
        db,
        "admin.user_sessions_revoke",
        principal=admin,
        actor_type="admin",
        tenant_user_id=u.id,
        resource_type="user",
        resource_id=u.id,
        reason=body.reason,
        metadata={"count": n},
    )
    return Message(status="revoked", message=f"{n} session(s) revoked.")


@router.post("/users/{user_id}/notes", response_model=Message, summary="Add an internal note")
def add_note(
    user_id: uuid.UUID,
    body: NoteIn,
    db: Session = Depends(get_db, scope="function"),
    admin: Principal = Depends(require_admin),
) -> Message:
    u = _user(db, user_id)
    db.add(AdminNote(id=uuid.uuid4(), subject_user_id=u.id, author_id=admin.user.id, body=body.body))
    audit(
        db,
        "admin.note_added",
        principal=admin,
        actor_type="admin",
        tenant_user_id=u.id,
        resource_type="user",
        resource_id=u.id,
    )
    return Message(status="added")


# --- databases --------------------------------------------------------------------------------------


@router.get("/databases", summary="Search all databases")
def list_all_databases(
    db: Session = Depends(get_db, scope="function"),
    admin: Principal = Depends(require_admin),
    q: str | None = Query(None, max_length=120),
    status: str | None = Query(None, max_length=20),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
) -> dict[str, Any]:
    stmt = (
        select(Database, User.email, Project.name, InfrastructureHost.name)
        .join(User, User.id == Database.owner_id)
        .join(Project, Project.id == Database.project_id)
        .join(InfrastructureHost, InfrastructureHost.id == Database.host_id, isouter=True)
    )
    stmt = stmt.where(Database.status == status) if status else stmt.where(Database.status != "deleted")
    if q:
        conds = [
            Database.name.ilike(f"%{q}%"),
            User.email.ilike(f"%{q}%"),
            Database.route_id == q,
            Database.internal_database_name == q,
        ]
        with contextlib.suppress(ValueError):
            conds.append(Database.id == uuid.UUID(q))
        stmt = stmt.where(or_(*conds))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.order_by(Database.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {
        "items": [
            {
                "id": d.id,
                "name": d.name,
                "owner_id": d.owner_id,
                "owner_email": email,
                "project": pname,
                "status": d.status,
                "status_reason": d.status_reason,
                "postgres_version": d.postgres_version,
                "compute_profile": d.compute_profile,
                "allocated_storage_mb": d.allocated_storage_mb,
                "storage_used_bytes": d.storage_used_bytes,
                "connections": d.connections_current,
                "max_connections": d.max_connections,
                "host": host,
                "route_id": d.route_id,
                "instance": d.internal_database_name,
                "created_at": d.created_at,
                "suspended_by_admin": d.suspended_by_admin,
            }
            for d, email, pname, host in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _db(db: Session, database_id: uuid.UUID) -> Database:
    d = db.get(Database, database_id)
    if d is None:
        raise NotFound("Database not found.")
    return d


@router.get("/databases/{database_id}", summary="Database details (no credentials)")
def get_any_database(
    database_id: uuid.UUID, db: Session = Depends(get_db, scope="function"), admin: Principal = Depends(require_admin)
) -> dict[str, Any]:
    d = _db(db, database_id)
    owner = db.get(User, d.owner_id)
    host = db.get(InfrastructureHost, d.host_id) if d.host_id else None
    events = db.scalars(
        select(DatabaseEvent)
        .where(DatabaseEvent.database_id == d.id)
        .order_by(DatabaseEvent.created_at.desc())
        .limit(100)
    ).all()
    jobs = db.scalars(
        select(ProvisioningJob)
        .where(ProvisioningJob.database_id == d.id)
        .order_by(ProvisioningJob.created_at.desc())
        .limit(50)
    ).all()
    return {
        "database": database_service.to_out(db, d).model_dump(),
        "owner": {"id": owner.id, "email": owner.email, "status": owner.status} if owner else None,
        "placement": {
            "host": host.name if host else None,
            "provider": d.provider,
            "region": d.region,
            "instance": d.internal_database_name,
            "route_id": d.route_id,
            "internal_endpoint": f"{d.internal_endpoint_host}:{d.internal_endpoint_port}"
            if d.internal_endpoint_host
            else None,
        },
        "events": [
            {
                "id": e.id,
                "event_type": e.event_type,
                "from_status": e.from_status,
                "to_status": e.to_status,
                "message": e.message,
                "actor_type": e.actor_type,
                "created_at": e.created_at,
            }
            for e in events
        ],
        "jobs": [
            {
                "id": j.id,
                "kind": j.kind,
                "status": j.status,
                "step": j.step,
                "attempt_count": j.attempt_count,
                "max_attempts": j.max_attempts,
                "error_code": j.error_code,
                "safe_error_message": j.safe_error_message,
                "created_at": j.created_at,
                "started_at": j.started_at,
                "completed_at": j.completed_at,
                "run_after": j.run_after,
            }
            for j in jobs
        ],
        "backups": [
            backup_service.backup_out(b, d).model_dump()
            for b in db.scalars(
                select(Backup).where(Backup.database_id == d.id).order_by(Backup.created_at.desc()).limit(30)
            )
        ],
    }


@router.post("/databases/{database_id}/retry", response_model=Message, summary="Retry failed provisioning")
def admin_retry(
    database_id: uuid.UUID,
    body: ReasonIn,
    db: Session = Depends(get_db, scope="function"),
    admin: Principal = Depends(require_admin),
) -> Message:
    d = _db(db, database_id)
    database_service.retry_failed(db, d, actor_type="admin")
    audit(
        db,
        "admin.database_retry",
        principal=admin,
        actor_type="admin",
        tenant_user_id=d.owner_id,
        resource_type="database",
        resource_id=d.id,
        reason=body.reason,
    )
    wake_workers()
    return Message(status="queued")


@router.post("/databases/{database_id}/suspend", response_model=Message, summary="Suspend (stop) a database")
def admin_suspend_db(
    database_id: uuid.UUID,
    body: ReasonIn,
    db: Session = Depends(get_db, scope="function"),
    admin: Principal = Depends(require_admin),
) -> Message:
    require_recent_auth(admin)
    d = _db(db, database_id)
    if d.status != "ready":
        raise Conflict("Only ready databases can be suspended.", code="invalid_state")
    d.suspended_by_admin = True
    sm.transition(db, d, "stopping", reason="Suspended by DatabaseMax support", actor_type="admin")
    database_service.create_job(db, d, "stop", payload={"reason": "admin"})
    audit(
        db,
        "admin.database_suspend",
        principal=admin,
        actor_type="admin",
        tenant_user_id=d.owner_id,
        resource_type="database",
        resource_id=d.id,
        reason=body.reason,
    )
    notify(
        db,
        d.owner_id,
        "admin_action",
        f"Database “{d.name}” was suspended by support",
        "Contact support for details.",
        severity="error",
        link=f"/databases/{d.id}",
        dedupe_key=f"admin-suspend:{d.id}:{utcnow():%Y%m%d%H%M}",
    )
    wake_workers()
    return Message(status="stopping")


@router.post("/databases/{database_id}/unsuspend", response_model=Message, summary="Lift suspension and start")
def admin_unsuspend_db(
    database_id: uuid.UUID,
    body: ReasonIn,
    db: Session = Depends(get_db, scope="function"),
    admin: Principal = Depends(require_admin),
) -> Message:
    require_recent_auth(admin)
    d = _db(db, database_id)
    if not d.suspended_by_admin:
        return Message(status="unchanged")
    d.suspended_by_admin = False
    if d.status == "stopped":
        sm.transition(db, d, "queued", reason="Suspension lifted", actor_type="admin")
        database_service.create_job(db, d, "start")
    audit(
        db,
        "admin.database_unsuspend",
        principal=admin,
        actor_type="admin",
        tenant_user_id=d.owner_id,
        resource_type="database",
        resource_id=d.id,
        reason=body.reason,
    )
    wake_workers()
    return Message(status="starting")


@router.get("/jobs", summary="Provisioning queue and recent jobs")
def list_jobs(
    db: Session = Depends(get_db, scope="function"),
    admin: Principal = Depends(require_admin),
    status: Literal["queued", "running", "succeeded", "failed", "cancelled", "active"] | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    stmt = (
        select(ProvisioningJob, Database.name, User.email)
        .join(Database, Database.id == ProvisioningJob.database_id)
        .join(User, User.id == ProvisioningJob.owner_id)
    )
    if status == "active":
        stmt = stmt.where(ProvisioningJob.status.in_(("queued", "running")))
    elif status:
        stmt = stmt.where(ProvisioningJob.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(
        stmt.order_by(ProvisioningJob.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {
        "items": [
            {
                "id": j.id,
                "kind": j.kind,
                "status": j.status,
                "step": j.step,
                "database_id": j.database_id,
                "database_name": name,
                "owner_email": email,
                "attempt_count": j.attempt_count,
                "max_attempts": j.max_attempts,
                "error_code": j.error_code,
                "safe_error_message": j.safe_error_message,
                "created_at": j.created_at,
                "started_at": j.started_at,
                "completed_at": j.completed_at,
                "run_after": j.run_after,
                "lease_owner": j.lease_owner,
                "duration_seconds": (j.completed_at - j.created_at).total_seconds() if j.completed_at else None,
            }
            for j, name, email in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# --- infrastructure -----------------------------------------------------------------------------------


@router.get("/infrastructure", summary="Hosts, capacity, workers, queue and alerts")
def infrastructure(
    db: Session = Depends(get_db, scope="function"), admin: Principal = Depends(require_admin)
) -> dict[str, Any]:
    now = utcnow()
    prov = ProvisionerClient(timeout=10)
    live: dict[str, Any] | None
    try:
        live = prov.capacity()
        prov_ok = True
    except ProvisionerError:
        live, prov_ok = None, False
    gateway: dict[str, Any] | None = None
    gw_url = get_settings().gateway_status_url
    if gw_url:
        try:
            gateway = httpx.get(gw_url, timeout=2).json()
        except (httpx.HTTPError, ValueError):
            gateway = {"status": "unreachable"}
    hosts = db.scalars(select(InfrastructureHost)).all()
    workers = db.scalars(select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_seen_at.desc())).all()
    versions = db.execute(
        select(Database.postgres_version, func.count())
        .where(Database.status != "deleted")
        .group_by(Database.postgres_version)
    ).all()
    profiles = db.execute(
        select(Database.compute_profile, func.count())
        .where(Database.status != "deleted")
        .group_by(Database.compute_profile)
    ).all()
    queue = dict(
        db.execute(
            select(ProvisioningJob.status, func.count())
            .where(ProvisioningJob.status.in_(("queued", "running")))
            .group_by(ProvisioningJob.status)
        ).all()
    )
    oldest = db.scalar(select(func.min(ProvisioningJob.created_at)).where(ProvisioningJob.status == "queued"))
    alerts: list[dict[str, Any]] = []
    if not prov_ok:
        alerts.append({"severity": "critical", "message": "Provisioner unreachable"})
    alerts.extend(security_monitor.summary(db)["alerts"])
    for h in hosts:
        if h.status == "retired":
            continue
        cap = h.capacity_metadata or {}
        if h.status == "offline":
            n = db.scalar(select(func.count(Database.id)).where(Database.host_id == h.id, Database.status != "deleted"))
            alerts.append(
                {
                    "severity": "critical",
                    "message": f"Host {h.name} is offline — {n} database(s) on it are unreachable (not moved)",
                }
            )
            continue
        storage = cap.get("storage") or {}
        if storage.get("agent") == "unreachable":
            alerts.append(
                {
                    "severity": "critical",
                    "message": f"Host {h.name}: host agent unreachable (no creates, starts or resizes)",
                }
            )
        fs = storage.get("data_plane_fs") or {}
        if fs.get("fs_size_bytes"):
            used = fs["fs_used_bytes"] / fs["fs_size_bytes"]
            if used >= 0.8:
                alerts.append(
                    {
                        "severity": "critical" if used >= 0.9 else "warning",
                        "message": f"Host {h.name}: data-plane disk {used:.0%} full (volumes are thin-provisioned)",
                    }
                )
        bk = cap.get("backups") or {}
        if (
            bk.get("budget_mb")
            and bk.get("used_bytes") is not None
            and bk["used_bytes"] > 0.8 * bk["budget_mb"] * 2**20
        ):
            alerts.append({"severity": "warning", "message": f"Host {h.name}: local backup storage above 80%"})
        if cap.get("memory_budget_mb") and cap.get("allocated_memory_mb", 0) / cap["memory_budget_mb"] > 0.85:
            alerts.append({"severity": "warning", "message": f"Host {h.name} memory allocation above 85%"})
        if cap.get("disk_budget_mb") and cap.get("allocated_disk_mb", 0) / cap["disk_budget_mb"] > 0.85:
            alerts.append({"severity": "warning", "message": f"Host {h.name} disk allocation above 85%"})
    if not workers or now - workers[0].last_seen_at > dt.timedelta(minutes=2):
        alerts.append({"severity": "critical", "message": "No healthy worker heartbeat in the last 2 minutes"})
    if oldest and now - oldest > dt.timedelta(minutes=10):
        alerts.append({"severity": "warning", "message": "Provisioning queue has jobs older than 10 minutes"})
    missing = (
        db.scalar(
            select(func.count(func.distinct(DatabaseEvent.database_id))).where(
                DatabaseEvent.event_type == "instance_missing", DatabaseEvent.created_at >= now - dt.timedelta(hours=2)
            )
        )
        or 0
    )
    if missing:
        alerts.append({"severity": "critical", "message": f"{missing} ready database(s) have no running instance"})
    failed_1h = (
        db.scalar(
            select(func.count(ProvisioningJob.id)).where(
                ProvisioningJob.status == "failed", ProvisioningJob.completed_at >= now - dt.timedelta(hours=1)
            )
        )
        or 0
    )
    if failed_1h:
        alerts.append({"severity": "warning", "message": f"{failed_1h} provisioning job(s) failed in the last hour"})
    bo = backup_service.admin_overview(db)
    uncovered = bo["ready_databases"] - bo["ready_databases_with_recent_backup"]
    if uncovered > 0:
        alerts.append(
            {
                "severity": "warning",
                "message": f"{uncovered} running database(s) have no completed backup in the last 26 hours",
            }
        )
    failed_backups = bo["last_24h"].get("failed", 0)
    if failed_backups:
        alerts.append({"severity": "warning", "message": f"{failed_backups} backup(s) failed in the last 24 hours"})
    if bo["verification_30d"]["failed"]:
        alerts.append(
            {
                "severity": "critical",
                "message": f"{bo['verification_30d']['failed']} backup restore verification(s) failed in 30 days",
            }
        )
    return {
        "provisioner": {"reachable": prov_ok, "capacity": live},
        "gateway": gateway,
        "hosts": [_host_view(db, h) for h in hosts],
        "workers": [
            {
                "id": w.worker_id,
                "started_at": w.started_at,
                "last_seen_at": w.last_seen_at,
                "healthy": now - w.last_seen_at < dt.timedelta(minutes=2),
                "info": w.info,
            }
            for w in workers[:20]
        ],
        "versions": [{"version": v, "count": n} for v, n in versions],
        "profiles": [{"profile": p, "count": n} for p, n in profiles],
        "queue": {"queued": queue.get("queued", 0), "running": queue.get("running", 0), "oldest_queued_at": oldest},
        "backups": {**bo, "provisioner": (live or {}).get("backups")},
        "alerts": alerts,
    }


@router.get("/audit", summary="Platform-wide audit log")
def admin_audit(
    db: Session = Depends(get_db, scope="function"),
    admin: Principal = Depends(require_admin),
    actor_type: str | None = Query(None, max_length=16),
    action: str | None = Query(None, max_length=64),
    user_id: uuid.UUID | None = None,
    outcome: str | None = Query(None, max_length=16),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    stmt = select(AuditEvent)
    if actor_type:
        stmt = stmt.where(AuditEvent.actor_type == actor_type)
    if action:
        stmt = stmt.where(AuditEvent.action.startswith(action))
    if user_id:
        stmt = stmt.where(or_(AuditEvent.tenant_user_id == user_id, AuditEvent.actor_user_id == user_id))
    if outcome:
        stmt = stmt.where(AuditEvent.outcome == outcome)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(AuditEvent.created_at.desc()).offset((page - 1) * page_size).limit(page_size))
    return {"items": [audit_out(e) for e in rows], "total": total, "page": page, "page_size": page_size}


@router.get("/billing", summary="Subscriptions, payments and billing events")
def admin_billing(
    db: Session = Depends(get_db, scope="function"), admin: Principal = Depends(require_admin)
) -> dict[str, Any]:
    subs = db.execute(
        select(Subscription, User.email)
        .join(User, User.id == Subscription.user_id)
        .where(Subscription.plan != "free")
        .order_by(Subscription.updated_at.desc())
        .limit(200)
    ).all()
    events = db.scalars(select(BillingEvent).order_by(BillingEvent.received_at.desc()).limit(100)).all()
    pays = db.execute(
        select(Payment, User.email)
        .join(User, User.id == Payment.user_id)
        .order_by(Payment.created_at.desc())
        .limit(100)
    ).all()
    return {
        "billing_enabled": get_settings().paddle_enabled,
        "subscriptions": [
            {
                "user_id": s.user_id,
                "email": e,
                "plan": s.plan,
                "status": s.status,
                "period_end": s.period_end,
                "cancel_at_period_end": s.cancel_at_period_end,
                "grace_until": s.grace_until,
            }
            for s, e in subs
        ],
        "payments": [
            {
                "id": p.id,
                "email": e,
                "status": p.status,
                "amount_cents": p.amount_cents,
                "currency": p.currency,
                "billed_at": p.billed_at,
            }
            for p, e in pays
        ],
        "events": [
            {
                "id": ev.id,
                "event_type": ev.event_type,
                "status": ev.status,
                "received_at": ev.received_at,
                "user_id": ev.user_id,
                "error": ev.error,
            }
            for ev in events
        ],
    }


@router.get("/admins", summary="Admin role assignments")
def list_admins(
    db: Session = Depends(get_db, scope="function"), admin: Principal = Depends(require_admin)
) -> list[dict[str, Any]]:
    rows = db.execute(
        select(AdminRoleAssignment, User.email)
        .join(User, User.id == AdminRoleAssignment.user_id)
        .order_by(AdminRoleAssignment.granted_at.desc())
    ).all()
    return [
        {
            "id": a.id,
            "user_id": a.user_id,
            "email": e,
            "role": a.role,
            "reason": a.reason,
            "grant_source": a.grant_source,
            "granted_at": a.granted_at,
            "revoked_at": a.revoked_at,
        }
        for a, e in rows
    ]


# --- backups ------------------------------------------------------------------------------------


@router.get("/backups", summary="All backups with status, size and verification")
def admin_backups(
    db: Session = Depends(get_db, scope="function"),
    admin: Principal = Depends(require_admin),
    status: str | None = Query(None, max_length=16),
    kind: str | None = Query(None, max_length=16),
    verification: Literal["passed", "failed", "none"] | None = None,
    q: str | None = Query(None, max_length=120),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    stmt = (
        select(Backup, Database.name, Database.status, User.email)
        .join(Database, Database.id == Backup.database_id)
        .join(User, User.id == Backup.owner_id)
    )
    if status:
        stmt = stmt.where(Backup.status == status)
    if kind:
        stmt = stmt.where(Backup.kind == kind)
    if verification == "none":
        stmt = stmt.where(Backup.verification_status.is_(None))
    elif verification:
        stmt = stmt.where(Backup.verification_status == verification)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Database.name.ilike(like), User.email.ilike(like), cast(Backup.id, String) == q))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.order_by(Backup.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {
        "overview": backup_service.admin_overview(db),
        "items": [
            {
                "id": b.id,
                "database_id": b.database_id,
                "database_name": dname,
                "database_status": dstatus,
                "owner_email": email,
                "kind": b.kind,
                "status": b.status,
                "size_bytes": b.size,
                "table_count": b.table_count,
                "store": b.store,
                "encryption_key_id": b.encryption_key_id,
                "created_at": b.created_at,
                "completed_at": b.completed_at,
                "duration_ms": b.duration_ms,
                "expires_at": b.expires_at,
                "verified_at": b.verified_at,
                "verification_status": b.verification_status,
                "verification_error": b.verification_error,
                "error_message": b.error_message,
            }
            for b, dname, dstatus, email in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/backups/{backup_id}/verify", response_model=Message, summary="Run a restore verification now")
def admin_verify_backup(
    backup_id: uuid.UUID,
    body: ReasonIn,
    db: Session = Depends(get_db, scope="function"),
    admin: Principal = Depends(require_admin),
) -> Message:
    require_recent_auth(admin)
    b = db.get(Backup, backup_id)
    if b is None:
        raise NotFound("Backup not found.")
    d = _db(db, b.database_id)
    backup_service.request_verification(db, d, b)
    audit(
        db,
        "admin.backup_verify",
        principal=admin,
        actor_type="admin",
        tenant_user_id=b.owner_id,
        resource_type="backup",
        resource_id=b.id,
        reason=body.reason,
    )
    wake_workers()
    return Message(status="queued")


@router.post("/databases/{database_id}/backup", response_model=Message, summary="Take a backup now")
def admin_backup_now(
    database_id: uuid.UUID,
    body: ReasonIn,
    db: Session = Depends(get_db, scope="function"),
    admin: Principal = Depends(require_admin),
) -> Message:
    require_recent_auth(admin)
    d = _db(db, database_id)
    b, _ = backup_service.request_backup(db, d, kind="scheduled")
    audit(
        db,
        "admin.backup_create",
        principal=admin,
        actor_type="admin",
        tenant_user_id=d.owner_id,
        resource_type="database",
        resource_id=d.id,
        reason=body.reason,
        metadata={"backup_id": str(b.id)},
    )
    wake_workers()
    return Message(status="queued")


# --- security monitoring --------------------------------------------------------------------------


@router.get("/security", summary="Security signals: failed sign-ins, sensitive actions, alerts")
def admin_security(
    db: Session = Depends(get_db, scope="function"), admin: Principal = Depends(require_admin)
) -> dict[str, Any]:
    return {
        **security_monitor.summary(db),
        "recent_events": [audit_out(e).model_dump() for e in security_monitor.recent_events(db)],
    }


# --- hosts (multi-node) ---------------------------------------------------------------------------


def _host_view(db: Session, h: InfrastructureHost) -> dict[str, Any]:
    from app.provisioning.hosts import is_fresh
    from app.provisioning.placement import allocations

    counts = dict(
        db.execute(
            select(Database.status, func.count(Database.id))
            .where(Database.host_id == h.id, Database.status != "deleted")
            .group_by(Database.status)
        ).all()
    )
    return {
        "id": h.id,
        "name": h.name,
        "provider": h.provider,
        "region": h.region,
        "status": h.status,
        "status_reason": h.status_reason,
        "status_changed_at": h.status_changed_at,
        "endpoint": h.endpoint,
        "labels": h.labels,
        "last_heartbeat_at": h.last_heartbeat_at,
        "heartbeat_fresh": is_fresh(h),
        "last_error": h.last_error,
        "token_registered": h.token_secret_reference is not None,
        "capacity": {k: v for k, v in (h.capacity_metadata or {}).items() if not k.startswith("_")},
        "allocated": allocations(db, h.id),
        "databases": {"total": sum(counts.values()), "by_status": counts},
    }


def _host(db: Session, host_id: uuid.UUID) -> InfrastructureHost:
    h = db.get(InfrastructureHost, host_id)
    if h is None:
        raise NotFound("Host not found.")
    return h


@router.get("/hosts", summary="Registered hosts, health and allocation")
def list_hosts(
    db: Session = Depends(get_db, scope="function"), admin: Principal = Depends(require_admin)
) -> dict[str, Any]:
    hosts = db.scalars(select(InfrastructureHost).order_by(InfrastructureHost.name)).all()
    return {"items": [_host_view(db, h) for h in hosts]}


class HostActionIn(ReasonIn):
    action: Literal["drain", "undrain", "retire"]


@router.post("/hosts/{host_id}/status", response_model=Message, summary="Drain, undrain or retire a host")
def set_host_status(
    host_id: uuid.UUID,
    body: HostActionIn,
    db: Session = Depends(get_db, scope="function"),
    admin: Principal = Depends(require_admin),
) -> Message:
    require_recent_auth(admin)
    h = _host(db, host_id)
    if body.action == "drain":
        if h.status not in ("active", "offline"):
            raise Conflict(f"Host is {h.status}.", code="invalid_state")
        if h.status == "offline":  # comes back as draining
            h.capacity_metadata = {**(h.capacity_metadata or {}), "_status_before_offline": "draining"}
        else:
            h.status = "draining"
    elif body.action == "undrain":
        if h.status != "draining":
            raise Conflict("Only a draining host can be undrained.", code="invalid_state")
        h.status = "active"
    else:
        live = db.scalar(
            select(func.count(Database.id)).where(
                Database.host_id == h.id, Database.status.notin_(("deleted", "failed"))
            )
        )
        if live:
            raise Conflict(f"Move or delete the {live} database(s) on this host first.", code="host_not_empty")
        h.status = "retired"
    h.status_reason = body.reason
    h.status_changed_at = utcnow()
    audit(
        db,
        f"admin.host_{body.action}",
        principal=admin,
        actor_type="admin",
        resource_type="host",
        resource_id=h.id,
        reason=body.reason,
        metadata={"host": h.name},
    )
    return Message(status=h.status)


class MoveIn(ReasonIn):
    target_host_id: uuid.UUID


@router.post("/databases/{database_id}/move", response_model=Message, summary="Move a database to another host")
def move_database(
    database_id: uuid.UUID,
    body: MoveIn,
    db: Session = Depends(get_db, scope="function"),
    admin: Principal = Depends(require_admin),
) -> Message:
    """Explicit, operator-initiated move (backup → restore on the target, same URL/credentials).
    The database is unavailable for writes, then briefly for connections, while it moves."""
    from app.provisioning.placement import NoCapacity, choose_host

    require_recent_auth(admin)
    d = _db(db, database_id)
    if d.status != "ready":
        raise Conflict("Only a running database can be moved.", code="invalid_state")
    if d.host_id == body.target_host_id:
        raise Conflict("The database is already on that host.", code="invalid_state")
    target = _host(db, body.target_host_id)
    try:
        choose_host(
            db,
            region=d.region,
            profile=d.compute_profile,
            storage_mb=d.allocated_storage_mb,
            only=target.id,
        )
    except NoCapacity as e:
        raise Conflict(f"The target host can't take this database: {e}", code="target_unavailable") from e
    d.migration_target_host_id = target.id
    sm.transition(db, d, "migrating", reason="Moving to another host", actor_type="admin")
    database_service.create_job(db, d, "migrate", payload={"reason": body.reason})
    audit(
        db,
        "admin.database_move",
        principal=admin,
        actor_type="admin",
        tenant_user_id=d.owner_id,
        resource_type="database",
        resource_id=d.id,
        reason=body.reason,
        metadata={"from_host": str(d.host_id), "to_host": str(target.id)},
    )
    notify(
        db,
        d.owner_id,
        "admin_action",
        f"Database “{d.name}” is being moved",
        "We're moving your database to another server. It is read-only and then briefly unavailable; "
        "the connection URL and password stay the same.",
        severity="warning",
        link=f"/databases/{d.id}",
        dedupe_key=f"move:{d.id}:{utcnow():%Y%m%d%H%M}",
    )
    wake_workers()
    return Message(status="migrating")

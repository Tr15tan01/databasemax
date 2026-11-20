"""Usage, quotas, estimates and the dashboard summary. Every number is traceable to a
measurement (usage_records / pg stats), an allocation, or a count in the control database."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import (
    ApiRequestCounter,
    AuditEvent,
    Database,
    DatabaseEvent,
    Plan,
    ProvisioningJob,
    Subscription,
    UsageRecord,
    utcnow,
)
from app.monitoring.metrics import api_requests, project_breakdown
from app.security.deps import Principal, require_scope
from app.services import plans

router = APIRouter(tags=["usage"])


def _pct(used: float, limit: float) -> float | None:
    return round(used / limit * 100, 1) if limit else None


def quotas(db: Session, principal: Principal) -> dict[str, Any]:
    uid = principal.user.id
    ent = plans.entitlements_for(db, uid)
    counts = plans.usage_counts(db, uid)
    total_storage_bytes = ent["max_total_storage_mb"] * 1024 * 1024
    return {
        "plan": {"id": ent.plan_id, "name": ent.plan_name, "status": ent.status},
        "items": [
            {
                "resource": "projects",
                "used": counts["projects"],
                "limit": ent["max_projects"],
                "unit": "count",
                "kind": "count",
                "percent": _pct(counts["projects"], ent["max_projects"]),
            },
            {
                "resource": "databases",
                "used": counts["databases"],
                "limit": ent["max_databases"],
                "unit": "count",
                "kind": "count",
                "percent": _pct(counts["databases"], ent["max_databases"]),
            },
            {
                "resource": "allocated_storage",
                "used": counts["allocated_storage_mb"],
                "limit": ent["max_total_storage_mb"],
                "unit": "MB",
                "kind": "allocated",
                "percent": _pct(counts["allocated_storage_mb"], ent["max_total_storage_mb"]),
            },
            {
                "resource": "storage_used",
                "used": counts["storage_used_bytes"],
                "limit": total_storage_bytes,
                "unit": "bytes",
                "kind": "measured",
                "percent": _pct(counts["storage_used_bytes"], total_storage_bytes),
            },
        ],
        "limits": ent.data,
    }


@router.get("/usage/quotas", summary="Plan limits and current usage")
def get_quotas(
    db: Session = Depends(get_db, scope="function"), principal: Principal = Depends(require_scope("usage:read"))
) -> dict[str, Any]:
    return quotas(db, principal)


def _period_bounds(sub: Subscription | None) -> tuple[dt.datetime, dt.datetime]:
    now = utcnow()
    if sub and sub.period_start and sub.period_end and sub.period_start <= now <= sub.period_end:
        return sub.period_start, sub.period_end
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (start + dt.timedelta(days=32)).replace(day=1)
    return start, end


@router.get("/usage/current", summary="Current-period usage")
def current_usage(
    db: Session = Depends(get_db, scope="function"), principal: Principal = Depends(require_scope("usage:read"))
) -> dict[str, Any]:
    uid = principal.user.id
    sub = db.scalar(select(Subscription).where(Subscription.user_id == uid))
    start, end = _period_bounds(sub)
    prev_start = (start - dt.timedelta(days=1)).replace(day=1)
    counts = plans.usage_counts(db, uid)
    conns = (
        db.scalar(
            select(func.coalesce(func.sum(Database.connections_current), 0)).where(
                Database.owner_id == uid, Database.status == "ready"
            )
        )
        or 0
    )
    measured_at = db.scalar(select(func.max(Database.metrics_measured_at)).where(Database.owner_id == uid))
    dbs = db.execute(
        select(
            Database.id,
            Database.name,
            Database.status,
            Database.allocated_storage_mb,
            Database.storage_used_bytes,
            Database.connections_current,
            Database.max_connections,
            Database.metrics_measured_at,
            Database.project_id,
        )
        .where(Database.owner_id == uid, Database.status != "deleted")
        .order_by(Database.name)
    ).all()
    peak_storage = db.scalar(
        select(func.max(UsageRecord.quantity)).where(
            UsageRecord.user_id == uid, UsageRecord.metric == "account_storage_bytes", UsageRecord.measured_at >= start
        )
    )
    prev_peak = db.scalar(
        select(func.max(UsageRecord.quantity)).where(
            UsageRecord.user_id == uid,
            UsageRecord.metric == "account_storage_bytes",
            UsageRecord.measured_at >= prev_start,
            UsageRecord.measured_at < start,
        )
    )
    return {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "measured_at": measured_at.isoformat() if measured_at else None,
        "projects": counts["projects"],
        "databases": counts["databases"],
        "allocated_storage_mb": counts["allocated_storage_mb"],
        "storage_used_bytes": counts["storage_used_bytes"],
        "active_connections": int(conns),
        "api_requests": {
            "current_period": api_requests(db, uid, start),
            "previous_period": api_requests(db, uid, prev_start) - api_requests(db, uid, start),
        },
        "peak_storage_bytes": {"current_period": peak_storage, "previous_period": prev_peak},
        "by_project": project_breakdown(db, uid),
        "by_database": [
            {
                "database_id": r[0],
                "name": r[1],
                "status": r[2],
                "allocated_storage_mb": r[3],
                "storage_used_bytes": r[4],
                "connections": r[5],
                "max_connections": r[6],
                "measured_at": r[7].isoformat() if r[7] else None,
                "project_id": r[8],
                "storage_percent": _pct(r[4] or 0, r[3] * 1024 * 1024) if r[4] is not None else None,
            }
            for r in dbs
        ],
        "notes": {
            "storage_used_bytes": "measured with pg_database_size every few minutes",
            "allocated_storage_mb": "reserved by your plan allocation, not measured",
            "active_connections": "measured from pg_stat_activity at the last sample",
        },
    }


HistoryMetric = Literal["storage_bytes", "connections", "api_requests", "databases", "memory_bytes", "cpu_percent"]


@router.get("/usage/history", summary="Historical usage time series (account level)")
def usage_history(
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_scope("usage:read")),
    metric: HistoryMetric = "storage_bytes",
    days: int = Query(30, ge=1, le=365),
    bucket: Literal["hour", "day"] = "day",
) -> dict[str, Any]:
    uid = principal.user.id
    since = utcnow() - dt.timedelta(days=days)
    if metric == "api_requests":
        rows = db.execute(
            select(func.date_trunc(bucket, ApiRequestCounter.hour).label("t"), func.sum(ApiRequestCounter.count))
            .where(ApiRequestCounter.user_id == uid, ApiRequestCounter.hour >= since)
            .group_by(text("1"))
            .order_by(text("1"))
        ).all()
        pts = [{"t": r[0].isoformat(), "v": float(r[1])} for r in rows]
        return {"metric": metric, "unit": "count", "source": "measured", "bucket": bucket, "points": pts}
    if metric in ("storage_bytes", "databases"):
        m = "account_storage_bytes" if metric == "storage_bytes" else "databases"
        rows = db.execute(
            select(func.date_trunc(bucket, UsageRecord.measured_at), func.max(UsageRecord.quantity))
            .where(
                UsageRecord.user_id == uid,
                UsageRecord.metric == m,
                UsageRecord.measured_at >= since,
                UsageRecord.database_id.is_(None),
            )
            .group_by(text("1"))
            .order_by(text("1"))
        ).all()
    else:
        # Sum across databases per sample, then take the max per bucket.
        inner = (
            select(UsageRecord.measured_at.label("ts"), func.sum(UsageRecord.quantity).label("q"))
            .where(
                UsageRecord.user_id == uid,
                UsageRecord.metric == metric,
                UsageRecord.measured_at >= since,
                UsageRecord.database_id.is_not(None),
            )
            .group_by(UsageRecord.measured_at)
            .subquery()
        )
        rows = db.execute(
            select(func.date_trunc(bucket, inner.c.ts), func.max(inner.c.q)).group_by(text("1")).order_by(text("1"))
        ).all()
    unit = {
        "storage_bytes": "bytes",
        "memory_bytes": "bytes",
        "connections": "count",
        "databases": "count",
        "cpu_percent": "percent",
    }[metric]
    return {
        "metric": metric,
        "unit": unit,
        "source": "measured",
        "bucket": bucket,
        "points": [{"t": r[0].isoformat(), "v": float(r[1])} for r in rows],
    }


@router.get("/usage/estimate", summary="Billing estimate for the current period")
def estimate(
    db: Session = Depends(get_db, scope="function"), principal: Principal = Depends(require_scope("usage:read"))
) -> dict[str, Any]:
    uid = principal.user.id
    sub = db.scalar(select(Subscription).where(Subscription.user_id == uid))
    ent = plans.entitlements_for(db, uid)
    plan = db.get(Plan, ent.plan_id)
    price = plan.price_cents if plan else 0
    renews = sub.period_end if sub and sub.plan != "free" and not sub.cancel_at_period_end else None
    return {
        "plan": ent.plan_id,
        "currency": "USD",
        "amount_cents": price if renews else 0,
        "next_billing_date": renews.isoformat() if renews else None,
        "kind": "estimated",
        "explanation": "Plans are billed at a flat monthly price; usage within plan limits doesn't change the "
        "amount. Taxes, if any, are calculated by Paddle at checkout.",
    }


@router.get("/dashboard/summary", summary="Everything the dashboard home needs in one call")
def dashboard_summary(
    db: Session = Depends(get_db, scope="function"), principal: Principal = Depends(require_scope("usage:read"))
) -> dict[str, Any]:
    uid = principal.user.id
    by_status = dict(
        db.execute(
            select(Database.status, func.count(Database.id))
            .where(Database.owner_id == uid, Database.status != "deleted")
            .group_by(Database.status)
        ).all()
    )
    provisioning = sum(
        v
        for k, v in by_status.items()
        if k in ("pending", "validating", "queued", "provisioning", "configuring", "testing")
    )
    q = quotas(db, principal)
    cur = current_usage(db, principal)
    est = estimate(db, principal)
    recent_activity = db.scalars(
        select(AuditEvent).where(AuditEvent.tenant_user_id == uid).order_by(AuditEvent.created_at.desc()).limit(8)
    ).all()
    recent_events = db.execute(
        select(DatabaseEvent, Database.name)
        .join(Database, Database.id == DatabaseEvent.database_id)
        .where(DatabaseEvent.owner_id == uid)
        .order_by(DatabaseEvent.created_at.desc())
        .limit(8)
    ).all()
    jobs_30d = (
        db.scalar(
            select(func.count(ProvisioningJob.id)).where(
                ProvisioningJob.owner_id == uid, ProvisioningJob.created_at >= utcnow() - dt.timedelta(days=30)
            )
        )
        or 0
    )
    return {
        "counts": {
            "projects": cur["projects"],
            "databases": cur["databases"],
            "running": by_status.get("ready", 0),
            "provisioning": provisioning,
            "failed": by_status.get("failed", 0),
            "stopped": by_status.get("stopped", 0),
            "provisioning_jobs_30d": jobs_30d,
        },
        "storage": {
            "allocated_mb": cur["allocated_storage_mb"],
            "used_bytes": cur["storage_used_bytes"],
            "measured_at": cur["measured_at"],
        },
        "connections": {"active": cur["active_connections"], "measured_at": cur["measured_at"]},
        "api_requests": cur["api_requests"],
        "quotas": q,
        "estimate": est,
        "recent_activity": [
            {
                "id": str(a.id),
                "action": a.action,
                "actor_type": a.actor_type,
                "resource_type": a.resource_type,
                "resource_id": a.resource_id,
                "outcome": a.outcome,
                "created_at": a.created_at.isoformat(),
            }
            for a in recent_activity
        ],
        "recent_database_events": [
            {
                "id": str(e.id),
                "database_id": str(e.database_id),
                "database_name": name,
                "event_type": e.event_type,
                "to_status": e.to_status,
                "message": e.message,
                "created_at": e.created_at.isoformat(),
            }
            for e, name in recent_events
        ],
        "backups": _backup_stats(db, uid),
    }


def _backup_stats(db: Session, uid: uuid.UUID) -> dict[str, Any]:
    from app.models import Backup
    from app.services.backup_service import RESTORABLE

    restorable = (Backup.owner_id == uid, Backup.status.in_(RESTORABLE))
    last = db.scalar(select(func.max(Backup.completed_at)).where(*restorable))
    failed_24h = (
        db.scalar(
            select(func.count(Backup.id)).where(
                Backup.owner_id == uid,
                Backup.status == "failed",
                Backup.created_at >= utcnow() - dt.timedelta(hours=24),
            )
        )
        or 0
    )
    return {
        "count": db.scalar(select(func.count(Backup.id)).where(*restorable)) or 0,
        "stored_bytes": int(db.scalar(select(func.coalesce(func.sum(Backup.size), 0)).where(*restorable)) or 0),
        "last_completed_at": last.isoformat() if last else None,
        "failed_24h": failed_24h,
        "retention_days": int(plans.entitlements_for(db, uid).get("backup_retention_days", 7)),
    }

from __future__ import annotations

import datetime as dt
import itertools
import uuid
from collections.abc import Sequence
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import ApiError
from app.core.ratelimit import hit
from app.core.redis import wake_workers
from app.models import Database, DatabaseEvent, Project, ProvisioningJob, UsageRecord, utcnow
from app.provisioning.connection import build_url, connection_info, masked_url
from app.schemas.common import Page
from app.schemas.resources import (
    ConnectionOut,
    DatabaseCreate,
    DatabaseEventOut,
    DatabaseOut,
    DatabaseUpdate,
    OptionsOut,
    ProvisioningStatus,
)
from app.security.deps import Principal, require_recent_auth, require_scope, require_verified
from app.security.secrets_store import secret_store
from app.services import approvals, idempotency, plans
from app.services import database_service as svc
from app.services.audit import audit

router = APIRouter(prefix="/databases", tags=["databases"])

SORTS = {
    "created_at": Database.created_at,
    "name": func.lower(Database.name),
    "status": Database.status,
    "storage": Database.storage_used_bytes,
    "last_activity": Database.last_activity_at,
}


@router.get("/options", response_model=OptionsOut, summary="Versions, sizes and limits available to you")
def options(
    db: Session = Depends(get_db, scope="function"), principal: Principal = Depends(require_scope("databases:read"))
) -> OptionsOut:
    s = get_settings()
    ent = plans.entitlements_for(db, principal.user.id)
    counts = plans.usage_counts(db, principal.user.id)
    return OptionsOut(
        postgres_versions=s.supported_pg_versions,
        default_postgres_version=s.default_pg_version,
        regions=[{"id": s.region, "label": s.region_label}],
        compute_profiles=[{"id": k, **v, "available": k in ent["compute_profiles"]} for k, v in plans.PROFILES.items()],
        storage={
            "max_per_database_mb": ent["max_storage_per_db_mb"],
            "total_mb": ent["max_total_storage_mb"],
            "allocated_mb": counts["allocated_storage_mb"],
            "remaining_mb": max(ent["max_total_storage_mb"] - counts["allocated_storage_mb"], 0),
        },
        max_connections_per_db=ent["max_connections_per_db"],
        plan=ent.plan_id,
    )


@router.post("", status_code=202, response_model=DatabaseOut, summary="Create a database (asynchronous)")
def create_database(
    body: DatabaseCreate,
    response: Response,
    background: BackgroundTasks,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_scope("databases:write")),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> DatabaseOut:
    require_verified(principal)
    rec, replay = idempotency.begin(
        db, principal.user.id, idempotency_key, "databases.create", body.model_dump(mode="json")
    )
    if replay:
        response.status_code = replay[0]
        response.headers["Idempotent-Replayed"] = "true"
        d = svc.get_database_for(db, principal, uuid.UUID(replay[1]["id"]), include_deleted=True)
        return svc.to_out(db, d)
    d = svc.create_database(db, principal, body, idempotency_key=idempotency.validate_key(idempotency_key))
    out = svc.to_out(db, d)
    idempotency.complete(rec, 202, {"id": str(d.id)})
    response.headers["Location"] = f"/api/v1/databases/{d.id}"
    background.add_task(wake_workers)
    return out


@router.get("", response_model=Page[DatabaseOut], summary="List databases")
def list_databases(
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_scope("databases:read")),
    q: str | None = Query(None, max_length=64),
    project_id: uuid.UUID | None = None,
    status: str | None = Query(None, max_length=20),
    environment: str | None = Query(None, max_length=20),
    sort: Literal["created_at", "name", "status", "storage", "last_activity"] = "created_at",
    order: Literal["asc", "desc"] = "desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> Page[DatabaseOut]:
    stmt = (
        select(Database, Project.name)
        .join(Project, Project.id == Database.project_id)
        .where(Database.owner_id == principal.user.id, Database.status != "deleted")
    )
    if principal.project_ids is not None:
        stmt = stmt.where(Database.project_id.in_(principal.project_ids))
    if project_id:
        stmt = stmt.where(Database.project_id == project_id)
    if status:
        group = {
            "running": ["ready"],
            "provisioning": ["pending", "validating", "queued", "provisioning", "configuring", "testing"],
        }.get(status, [status])
        stmt = stmt.where(Database.status.in_(group))
    if environment:
        stmt = stmt.where(Database.environment == environment)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(Database.name.ilike(like), Database.pg_database_name.ilike(like), Project.name.ilike(like))
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    col = SORTS[sort]
    stmt = stmt.order_by(col.asc().nulls_last() if order == "asc" else col.desc().nulls_last(), Database.id)
    rows = db.execute(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    return Page(
        items=[svc.to_out(db, d, project_name=pn) for d, pn in rows], total=total, page=page, page_size=page_size
    )


@router.get("/{database_id}", response_model=DatabaseOut, summary="Get a database")
def get_database(
    database_id: uuid.UUID,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_scope("databases:read")),
) -> DatabaseOut:
    return svc.to_out(db, svc.get_database_for(db, principal, database_id))


@router.get("/{database_id}/status", response_model=ProvisioningStatus, summary="Provisioning / lifecycle status")
def get_status(
    database_id: uuid.UUID,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_scope("databases:read")),
) -> ProvisioningStatus:
    d = svc.get_database_for(db, principal, database_id, include_deleted=True)
    return svc.provisioning_status(d, svc.latest_job(db, d.id))


@router.patch("/{database_id}", response_model=DatabaseOut, summary="Update database settings")
def update_database(
    database_id: uuid.UUID,
    body: DatabaseUpdate,
    background: BackgroundTasks,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_scope("databases:write")),
) -> DatabaseOut:
    d = svc.get_database_for(db, principal, database_id, lock=True)
    svc.update_database(db, principal, d, body)
    background.add_task(wake_workers)
    return svc.to_out(db, d)


@router.delete("/{database_id}", status_code=202, response_model=DatabaseOut, summary="Delete a database")
def delete_database(
    database_id: uuid.UUID,
    background: BackgroundTasks,
    confirm: str = Query(..., max_length=64, description="The database name, to confirm"),
    final_backup: bool = Query(
        False,
        description="Take an encrypted backup first and keep it for the plan's "
        "retention period (database must be running)",
    ),
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_scope("databases:delete")),
) -> DatabaseOut:
    d = svc.get_database_for(db, principal, database_id, lock=True)
    if confirm != d.name:
        raise ApiError("Type the database name exactly to confirm deletion.", code="confirmation_mismatch")
    if principal.actor_type == "user":
        require_recent_auth(principal)
    if d.status == "deleting":
        raise ApiError("This database is already being deleted.", code="invalid_state", status_code=409)
    approvals.gate(
        db,
        principal,
        "database.delete",
        resource_type="database",
        resource_id=d.id,
        summary=f"delete database “{d.name}”" + (" after a final backup" if final_backup else ""),
        params={"final_backup": final_backup},
    )
    svc.request_delete(db, principal, d, final_backup=final_backup)
    background.add_task(wake_workers)
    return svc.to_out(db, d)


@router.post("/{database_id}/stop", status_code=202, response_model=DatabaseOut, summary="Stop (pause) a database")
def stop_database(
    database_id: uuid.UUID,
    background: BackgroundTasks,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_scope("databases:write")),
) -> DatabaseOut:
    d = svc.get_database_for(db, principal, database_id, lock=True)
    svc.request_stop(db, principal, d)
    background.add_task(wake_workers)
    return svc.to_out(db, d)


@router.post("/{database_id}/start", status_code=202, response_model=DatabaseOut, summary="Start a stopped database")
def start_database(
    database_id: uuid.UUID,
    background: BackgroundTasks,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_scope("databases:write")),
) -> DatabaseOut:
    d = svc.get_database_for(db, principal, database_id, lock=True)
    svc.request_start(db, principal, d)
    background.add_task(wake_workers)
    return svc.to_out(db, d)


@router.post("/{database_id}/retry", status_code=202, response_model=DatabaseOut, summary="Retry failed provisioning")
def retry_database(
    database_id: uuid.UUID,
    background: BackgroundTasks,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_scope("databases:write")),
) -> DatabaseOut:
    d = svc.get_database_for(db, principal, database_id, lock=True)
    svc.retry_failed(db, d, actor_type=principal.actor_type)
    audit(db, "database.retry", principal=principal, resource_type="database", resource_id=d.id)
    background.add_task(wake_workers)
    return svc.to_out(db, d)


@router.post(
    "/{database_id}/credentials/rotate",
    status_code=202,
    response_model=ProvisioningStatus,
    summary="Rotate the database password (old password stops working)",
)
def rotate_credentials(
    database_id: uuid.UUID,
    background: BackgroundTasks,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_scope("credentials:rotate")),
) -> ProvisioningStatus:
    d = svc.get_database_for(db, principal, database_id, lock=True)
    ent = plans.entitlements_for(db, principal.user.id)
    if not ent.get("credential_rotation", False):
        raise ApiError("Credential rotation isn't available on your plan.", code="feature_unavailable", status_code=402)
    if d.status != "ready":
        raise ApiError("Credentials can be rotated while the database is ready.", code="invalid_state", status_code=409)
    approvals.gate(
        db,
        principal,
        "database.credentials_rotate",
        resource_type="database",
        resource_id=d.id,
        summary=f"rotate the password of database “{d.name}” (the current password stops working)",
    )
    job = svc.request_rotation(db, principal, d)
    background.add_task(wake_workers)
    return svc.provisioning_status(d, job)


@router.get("/{database_id}/connection", response_model=ConnectionOut, summary="Connection details")
def get_connection(
    database_id: uuid.UUID,
    reveal: bool = False,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_scope("databases:read")),
) -> ConnectionOut:
    d = svc.get_database_for(db, principal, database_id)
    cred = svc.active_credential(db, d)
    rotating = (
        db.scalar(
            select(ProvisioningJob.id).where(
                ProvisioningJob.database_id == d.id,
                ProvisioningJob.kind == "rotate",
                ProvisioningJob.status.in_(("queued", "running")),
            )
        )
        is not None
    )
    if cred is None:
        raise ApiError(
            "Connection details are available once the database is ready.", code="not_ready", status_code=409
        )
    info = connection_info(d, cred.username)
    password = None
    if reveal:
        if not principal.has_scope("credentials:read"):
            raise ApiError(
                "API key is missing the 'credentials:read' scope.", code="insufficient_scope", status_code=403
            )
        if principal.actor_type == "user":
            require_recent_auth(principal)
        hit("reveal", str(principal.user.id), 30, 3600)  # reveals are audited; bulk scraping is not normal use
        assert cred.secret_reference is not None
        password = secret_store.get(db, cred.secret_reference)
        cred.last_revealed_at = utcnow()
        audit(db, "database.credentials_revealed", principal=principal, resource_type="database", resource_id=d.id)
    return ConnectionOut(
        host=info.host,
        port=info.port,
        database=info.database,
        username=info.username,
        sslmode=info.sslmode,
        password=password,
        password_revealed=password is not None,
        url=build_url(info, password) if password else masked_url(info),
        url_masked=masked_url(info),
        credential_id=cred.id,
        credential_created_at=cred.activated_at or cred.created_at,
        ssl_note="TLS is required (sslmode=require). For certificate verification use sslmode=verify-full "
        "with the system CA bundle.",
        rotation_in_progress=rotating,
    )


@router.get("/{database_id}/events", response_model=Page[DatabaseEventOut], summary="Lifecycle events")
def get_events(
    database_id: uuid.UUID,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_scope("databases:read")),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> Page[DatabaseEventOut]:
    d = svc.get_database_for(db, principal, database_id, include_deleted=True)
    stmt = select(DatabaseEvent).where(DatabaseEvent.database_id == d.id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(DatabaseEvent.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return Page(items=[DatabaseEventOut.model_validate(r) for r in rows], total=total, page=page, page_size=page_size)


@router.get("/{database_id}/usage", summary="Measured usage time series for a database")
def get_usage(
    database_id: uuid.UUID,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_scope("usage:read")),
    metric: Literal[
        "storage_bytes",
        "connections",
        "memory_bytes",
        "cpu_percent",
        "transactions_total",
        "transactions_per_minute",
        "rows_read_per_minute",
        "rows_written_per_minute",
    ] = "storage_bytes",
    days: int = Query(7, ge=1, le=90),
) -> dict[str, object]:
    d = svc.get_database_for(db, principal, database_id, include_deleted=True)
    since = utcnow() - dt.timedelta(days=days)
    rows = db.execute(
        select(UsageRecord.measured_at, UsageRecord.quantity, UsageRecord.granularity)
        .where(
            UsageRecord.database_id == d.id,
            UsageRecord.metric == RATE_METRICS.get(metric, metric),
            UsageRecord.measured_at >= since,
        )
        .order_by(UsageRecord.measured_at)
    ).all()
    points = (
        _rates(rows)
        if metric in RATE_METRICS
        else [{"t": r[0].isoformat(), "v": r[1], "granularity": r[2]} for r in rows]
    )
    return {
        "database_id": str(d.id),
        "metric": metric,
        "source": "derived" if metric in RATE_METRICS else "measured",
        "unit": _unit(metric),
        "points": points,
        "current": {
            "storage_used_bytes": d.storage_used_bytes,
            "allocated_storage_mb": d.allocated_storage_mb,
            "connections": d.connections_current,
            "max_connections": d.max_connections,
            "measured_at": d.metrics_measured_at.isoformat() if d.metrics_measured_at else None,
        },
    }


# Per-minute rates derived from cumulative PostgreSQL counters (pg_stat_database).
RATE_METRICS = {
    "transactions_per_minute": "transactions_total",
    "rows_read_per_minute": "rows_read_total",
    "rows_written_per_minute": "rows_written_total",
}


def _rates(rows: Sequence[Any]) -> list[dict[str, object]]:
    """Rate between consecutive samples of a cumulative counter. A drop means the counter was reset
    (instance restart, stats reset): that interval is skipped rather than shown as negative."""
    out: list[dict[str, object]] = []
    for a, b in itertools.pairwise(rows):
        (t0, v0, _), (t1, v1, gran) = tuple(a), tuple(b)
        minutes = (t1 - t0).total_seconds() / 60
        if minutes <= 0 or v1 < v0:
            continue
        out.append({"t": t1.isoformat(), "v": round((v1 - v0) / minutes, 2), "granularity": gran})
    return out


def _unit(metric: str) -> str:
    if metric in RATE_METRICS:
        return "per_minute"
    return {
        "storage_bytes": "bytes",
        "memory_bytes": "bytes",
        "connections": "count",
        "cpu_percent": "percent",
        "transactions_total": "count",
    }[metric]

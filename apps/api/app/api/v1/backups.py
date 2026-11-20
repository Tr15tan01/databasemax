"""Backups and clones.

Backups are logical, encrypted snapshots taken daily and on demand. Restoring always creates a
new database (a clone); an existing database is never overwritten.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import Conflict
from app.core.redis import wake_workers
from app.models import Backup, Database
from app.schemas.common import Page
from app.schemas.resources import BackupOut, BackupSummaryOut, CloneCreate, DatabaseOut
from app.security.deps import Principal, require_recent_auth, require_scope, require_verified
from app.services import approvals
from app.services import backup_service as bsvc
from app.services import database_service as svc

router = APIRouter(tags=["backups"])


@router.get(
    "/databases/{database_id}/backups",
    response_model=Page[BackupOut],
    summary="List backups of a database",
)
def list_database_backups(
    database_id: uuid.UUID,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_scope("backups:read")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    include_expired: bool = False,
) -> Page[BackupOut]:
    d = svc.get_database_for(db, principal, database_id, include_deleted=True)
    stmt = select(Backup).where(Backup.database_id == d.id, Backup.status != "deleted")
    if not include_expired:
        stmt = stmt.where(Backup.status != "expired")
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Backup.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return Page(items=[bsvc.backup_out(b, d) for b in rows], total=total, page=page, page_size=page_size)


@router.get(
    "/databases/{database_id}/backups/summary",
    response_model=BackupSummaryOut,
    summary="Backup schedule, retention and totals for a database",
)
def backup_summary(
    database_id: uuid.UUID,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_scope("backups:read")),
) -> BackupSummaryOut:
    d = svc.get_database_for(db, principal, database_id, include_deleted=True)
    return bsvc.summary(db, d)


@router.post(
    "/databases/{database_id}/backups",
    status_code=202,
    response_model=BackupOut,
    summary="Take an on-demand backup (asynchronous)",
)
def create_backup(
    database_id: uuid.UUID,
    background: BackgroundTasks,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_scope("backups:write")),
) -> BackupOut:
    require_verified(principal)
    d = svc.get_database_for(db, principal, database_id, lock=True)
    b, _job = bsvc.request_backup(db, d, kind="manual", principal=principal)
    background.add_task(wake_workers)
    return bsvc.backup_out(b, d)


@router.get("/backups", response_model=Page[BackupOut], summary="List backups across all your databases")
def list_backups(
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_scope("backups:read")),
    database_id: uuid.UUID | None = None,
    status: Literal["restorable", "failed", "in_progress", "all"] = "restorable",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> Page[BackupOut]:
    stmt = bsvc.list_for_owner_stmt(principal)
    if database_id:
        stmt = stmt.where(Backup.database_id == database_id)
    if status == "restorable":
        stmt = stmt.where(Backup.status.in_(bsvc.RESTORABLE))
    elif status == "failed":
        stmt = stmt.where(Backup.status == "failed")
    elif status == "in_progress":
        stmt = stmt.where(Backup.status.in_(("pending", "running")))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.order_by(Backup.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return Page(items=[bsvc.backup_out(b, d) for b, d in rows], total=total, page=page, page_size=page_size)


@router.get("/backups/{backup_id}", response_model=BackupOut, summary="Get a backup")
def get_backup(
    backup_id: uuid.UUID,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_scope("backups:read")),
) -> BackupOut:
    b, d = bsvc.get_backup_for(db, principal, backup_id)
    return bsvc.backup_out(b, d)


@router.delete("/backups/{backup_id}", response_model=BackupOut, summary="Delete a backup (cannot be undone)")
def delete_backup(
    backup_id: uuid.UUID,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_scope("backups:write")),
) -> BackupOut:
    b, d = bsvc.get_backup_for(db, principal, backup_id)
    if principal.actor_type == "user":
        require_recent_auth(principal)
    if b.status in ("pending", "running"):
        raise Conflict("This backup is still running.", code="invalid_state")
    approvals.gate(
        db,
        principal,
        "backup.delete",
        resource_type="backup",
        resource_id=b.id,
        summary=f"delete a {b.kind} backup of database “{d.name}” from {b.created_at:%Y-%m-%d %H:%M} UTC",
    )
    bsvc.delete_backup(db, principal, b)
    return bsvc.backup_out(b, d)


@router.post(
    "/databases/{database_id}/clone",
    status_code=202,
    response_model=DatabaseOut,
    summary="Create a new database from a backup, or from the current data (asynchronous)",
    description="The new database gets its own instance, credentials and connection URL. The source "
    "database is never modified. Works for deleted databases while their backups are retained.",
)
def clone_database(
    database_id: uuid.UUID,
    body: CloneCreate,
    response: Response,
    background: BackgroundTasks,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_scope("databases:write")),
) -> DatabaseOut:
    require_verified(principal)
    if not principal.has_scope("backups:write"):
        from app.core.errors import ApiError

        raise ApiError("API key is missing the 'backups:write' scope.", code="insufficient_scope", status_code=403)
    source: Database = svc.get_database_for(db, principal, database_id, include_deleted=body.backup_id is not None)
    target = bsvc.request_clone(db, principal, source, body)
    response.headers["Location"] = f"/api/v1/databases/{target.id}"
    background.add_task(wake_workers)
    return svc.to_out(db, target)

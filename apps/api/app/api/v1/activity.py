from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import NotFound
from app.models import AuditEvent
from app.schemas.common import OutModel, Page
from app.security.deps import Principal, require_scope

router = APIRouter(prefix="/activity", tags=["activity"])


class AuditEventOut(OutModel):
    id: uuid.UUID
    action: str
    actor_type: str
    actor_user_id: uuid.UUID | None
    actor_api_key_id: uuid.UUID | None
    resource_type: str | None
    resource_id: str | None
    outcome: str
    reason: str | None
    request_id: str | None
    ip: str | None
    metadata: dict[str, Any]
    created_at: dt.datetime


def to_out(e: AuditEvent) -> AuditEventOut:
    return AuditEventOut(
        id=e.id,
        action=e.action,
        actor_type=e.actor_type,
        actor_user_id=e.actor_user_id,
        actor_api_key_id=e.actor_api_key_id,
        resource_type=e.resource_type,
        resource_id=e.resource_id,
        outcome=e.outcome,
        reason=e.reason,
        request_id=e.request_id,
        ip=str(e.ip) if e.ip else None,
        metadata=e.metadata_ or {},
        created_at=e.created_at,
    )


@router.get("", response_model=Page[AuditEventOut], summary="Your account's audit log")
def list_activity(
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_scope("activity:read")),
    action: str | None = Query(None, max_length=64),
    resource_type: str | None = Query(None, max_length=32),
    resource_id: str | None = Query(None, max_length=64),
    actor_type: str | None = Query(None, max_length=16),
    api_key_id: uuid.UUID | None = Query(None, description="Only actions performed with this API key"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> Page[AuditEventOut]:
    stmt = select(AuditEvent).where(AuditEvent.tenant_user_id == principal.user.id)
    if action:
        stmt = stmt.where(AuditEvent.action.startswith(action))
    if resource_type:
        stmt = stmt.where(AuditEvent.resource_type == resource_type)
    if resource_id:
        stmt = stmt.where(AuditEvent.resource_id == resource_id)
    if actor_type:
        stmt = stmt.where(AuditEvent.actor_type == actor_type)
    if api_key_id:
        stmt = stmt.where(AuditEvent.actor_api_key_id == api_key_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(AuditEvent.created_at.desc()).offset((page - 1) * page_size).limit(page_size))
    return Page(items=[to_out(e) for e in rows], total=total, page=page, page_size=page_size)


@router.get("/{event_id}", response_model=AuditEventOut, summary="A single audit event")
def get_activity(
    event_id: uuid.UUID,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_scope("activity:read")),
) -> AuditEventOut:
    e = db.scalar(select(AuditEvent).where(AuditEvent.id == event_id, AuditEvent.tenant_user_id == principal.user.id))
    if e is None:
        raise NotFound("Event not found.")
    return to_out(e)

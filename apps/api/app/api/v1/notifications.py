from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import NotFound
from app.models import Notification, utcnow
from app.schemas.common import Message, OutModel, Page
from app.security.deps import Principal, require_principal

router = APIRouter(prefix="/notifications", tags=["notifications"])


class NotificationOut(OutModel):
    id: uuid.UUID
    type: str
    severity: str
    title: str
    body: str
    link: str | None
    read_at: dt.datetime | None
    created_at: dt.datetime


@router.get("", response_model=Page[NotificationOut], summary="In-app notifications")
def list_notifications(
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_principal),
    unread_only: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> Page[NotificationOut]:
    stmt = select(Notification).where(Notification.user_id == principal.user.id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Notification.created_at.desc()).offset((page - 1) * page_size).limit(page_size))
    return Page(items=[NotificationOut.model_validate(n) for n in rows], total=total, page=page, page_size=page_size)


@router.get("/unread-count", summary="Unread notification count")
def unread_count(
    db: Session = Depends(get_db, scope="function"), principal: Principal = Depends(require_principal)
) -> dict[str, int]:
    n = (
        db.scalar(
            select(func.count(Notification.id)).where(
                Notification.user_id == principal.user.id, Notification.read_at.is_(None)
            )
        )
        or 0
    )
    return {"unread": n}


@router.post("/{notification_id}/read", response_model=Message, summary="Mark as read")
def mark_read(
    notification_id: uuid.UUID,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_principal),
) -> Message:
    n = db.scalar(
        select(Notification).where(Notification.id == notification_id, Notification.user_id == principal.user.id)
    )
    if n is None:
        raise NotFound("Notification not found.")
    n.read_at = n.read_at or utcnow()
    return Message(status="read")


@router.post("/read-all", response_model=Message, summary="Mark all as read")
def mark_all(
    db: Session = Depends(get_db, scope="function"), principal: Principal = Depends(require_principal)
) -> Message:
    db.execute(
        update(Notification)
        .where(Notification.user_id == principal.user.id, Notification.read_at.is_(None))
        .values(read_at=utcnow())
    )
    return Message(status="read")

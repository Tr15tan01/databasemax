"""Approval requests for destructive operations asked for by API keys (see services/approvals)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.redis import wake_workers
from app.models import ApiKey, ApprovalRequest, utcnow
from app.schemas.common import ApiModel, OutModel, Page
from app.security.deps import Principal, require_principal, require_recent_auth, require_session_user
from app.services import approvals as svc

router = APIRouter(prefix="/approvals", tags=["approvals"])


class ApprovalOut(OutModel):
    id: uuid.UUID
    action: str
    action_label: str
    resource_type: str
    resource_id: str
    summary: str
    params: dict[str, Any]
    status: str
    api_key_id: uuid.UUID | None
    api_key_name: str | None
    created_at: dt.datetime
    expires_at: dt.datetime
    decided_at: dt.datetime | None
    decision_reason: str | None
    result: dict[str, Any] | None
    approve_url: str


class ApprovalDecision(ApiModel):
    reason: str | None = Field(None, max_length=300)


class ApprovalCounts(OutModel):
    pending: int


def _names(db: Session, rows: list[ApprovalRequest]) -> dict[uuid.UUID, str]:
    ids = {r.api_key_id for r in rows if r.api_key_id}
    if not ids:
        return {}
    return {k.id: k.name for k in db.scalars(select(ApiKey).where(ApiKey.id.in_(ids)))}


def _out(r: ApprovalRequest, names: dict[uuid.UUID, str]) -> ApprovalOut:
    return ApprovalOut(**svc.to_out(r, names.get(r.api_key_id) if r.api_key_id else None))


def _expire_due(db: Session, owner_id: uuid.UUID) -> None:
    for r in db.scalars(
        select(ApprovalRequest).where(
            ApprovalRequest.owner_id == owner_id,
            ApprovalRequest.status == "pending",
            ApprovalRequest.expires_at <= utcnow(),
        )
    ):
        svc.expire_if_due(r)


@router.get("", response_model=Page[ApprovalOut], summary="Approval requests (API keys see only their own)")
def list_approvals(
    status: Literal["pending", "approved", "rejected", "expired", "executed", "failed"] | None = None,
    api_key_id: uuid.UUID | None = None,
    decided: bool = Query(False, description="Only requests that are no longer pending"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_principal),
) -> Page[ApprovalOut]:
    _expire_due(db, principal.user.id)
    db.flush()
    stmt = select(ApprovalRequest).where(ApprovalRequest.owner_id == principal.user.id)
    if principal.api_key is not None:
        stmt = stmt.where(ApprovalRequest.api_key_id == principal.api_key.id)
    elif api_key_id:
        stmt = stmt.where(ApprovalRequest.api_key_id == api_key_id)
    if status:
        stmt = stmt.where(ApprovalRequest.status == status)
    if decided:
        stmt = stmt.where(ApprovalRequest.status != "pending")
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        db.scalars(stmt.order_by(ApprovalRequest.created_at.desc()).offset((page - 1) * page_size).limit(page_size))
    )
    names = _names(db, rows)
    return Page(items=[_out(r, names) for r in rows], total=total, page=page, page_size=page_size)


@router.get("/count", response_model=ApprovalCounts, summary="Number of pending approval requests")
def count_pending(
    db: Session = Depends(get_db, scope="function"), principal: Principal = Depends(require_session_user)
) -> ApprovalCounts:
    n = db.scalar(
        select(func.count(ApprovalRequest.id)).where(
            ApprovalRequest.owner_id == principal.user.id,
            ApprovalRequest.status == "pending",
            ApprovalRequest.expires_at > utcnow(),
        )
    )
    return ApprovalCounts(pending=int(n or 0))


@router.get("/{approval_id}", response_model=ApprovalOut, summary="One approval request (poll it for the outcome)")
def get_approval(
    approval_id: uuid.UUID,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_principal),
) -> ApprovalOut:
    r = svc.get_for(db, principal, approval_id)
    return _out(r, _names(db, [r]))


@router.post(
    "/{approval_id}/approve",
    response_model=ApprovalOut,
    summary="Approve and run the operation (dashboard session with recent sign-in only)",
)
def approve(
    approval_id: uuid.UUID,
    body: ApprovalDecision,
    background: BackgroundTasks,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_session_user),
) -> ApprovalOut:
    require_recent_auth(principal)
    r = svc.get_for(db, principal, approval_id)
    svc.approve(db, principal, r, body.reason)
    background.add_task(wake_workers)
    return _out(r, _names(db, [r]))


@router.post("/{approval_id}/reject", response_model=ApprovalOut, summary="Reject the request (nothing is changed)")
def reject(
    approval_id: uuid.UUID,
    body: ApprovalDecision,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_session_user),
) -> ApprovalOut:
    r = svc.get_for(db, principal, approval_id)
    svc.reject(db, principal, r, body.reason)
    return _out(r, _names(db, [r]))

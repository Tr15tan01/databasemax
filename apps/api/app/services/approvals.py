"""Human approval for destructive operations requested by API keys.

Agent keys (machine identities) always need approval for the operations in DESTRUCTIVE; other
keys can opt in. Instead of running, the request is recorded as an ApprovalRequest, the owner
is notified, and the API answers `202 {"status": "pending_approval", ...}`. The owner approves
from a dashboard session with recent re-authentication, which executes the operation through
the same service functions (same validation, quotas and audit) as a direct request. API keys
can never approve anything.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ApiError, Conflict, NotFound, RateLimited
from app.models import ApprovalRequest, Project, utcnow
from app.security.deps import Principal
from app.services.audit import audit
from app.services.notifications import notify

TTL = dt.timedelta(hours=24)
MAX_PENDING_PER_KEY = 20  # bounds notification/email volume from a misbehaving agent

DESTRUCTIVE: dict[str, str] = {
    "database.delete": "Delete database",
    "database.credentials_rotate": "Rotate database password",
    "project.delete": "Delete project",
    "backup.delete": "Delete backup",
}


class ApprovalRequired(Exception):
    """Raised by gate(); rendered as `202 {"status": "pending_approval", "approval": {...}}`."""

    def __init__(self, request: ApprovalRequest, body: dict[str, Any]):
        super().__init__("approval required")
        self.request = request
        self.body = body


def needs_approval(principal: Principal) -> bool:
    k = principal.api_key
    return k is not None and (k.kind == "agent" or bool(k.requires_approval))


def to_out(r: ApprovalRequest, key_name: str | None = None) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "action": r.action,
        "action_label": DESTRUCTIVE.get(r.action, r.action),
        "resource_type": r.resource_type,
        "resource_id": r.resource_id,
        "summary": r.summary,
        "params": r.params,
        "status": r.status,
        "api_key_id": str(r.api_key_id) if r.api_key_id else None,
        "api_key_name": key_name,
        "created_at": r.created_at.isoformat(),
        "expires_at": r.expires_at.isoformat(),
        "decided_at": r.decided_at.isoformat() if r.decided_at else None,
        "decision_reason": r.decision_reason,
        "result": r.result,
        "approve_url": f"/approvals?id={r.id}",
    }


def gate(
    db: Session,
    principal: Principal,
    action: str,
    *,
    resource_type: str,
    resource_id: uuid.UUID | str,
    summary: str,
    params: dict[str, Any] | None = None,
) -> None:
    """Call before performing `action`. Returns when the caller may proceed; otherwise records
    (or reuses) a pending approval request and raises ApprovalRequired."""
    if action not in DESTRUCTIVE:
        raise ValueError(f"unknown destructive action {action}")
    if not needs_approval(principal):
        return
    assert principal.api_key is not None
    rid = str(resource_id)
    existing = db.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.owner_id == principal.user.id,
            ApprovalRequest.action == action,
            ApprovalRequest.resource_id == rid,
            ApprovalRequest.status == "pending",
            ApprovalRequest.expires_at > utcnow(),
        )
    )
    if existing is not None:
        raise ApprovalRequired(existing, to_out(existing, principal.api_key.name))
    open_for_key = db.scalar(
        select(func.count(ApprovalRequest.id)).where(
            ApprovalRequest.api_key_id == principal.api_key.id,
            ApprovalRequest.status == "pending",
            ApprovalRequest.expires_at > utcnow(),
        )
    )
    if (open_for_key or 0) >= MAX_PENDING_PER_KEY:
        raise RateLimited(
            f"This key already has {MAX_PENDING_PER_KEY} requests waiting for approval. "
            "Wait for the owner to decide, or ask them to reject stale requests."
        )
    req = ApprovalRequest(
        id=uuid.uuid4(),
        owner_id=principal.user.id,
        api_key_id=principal.api_key.id,
        action=action,
        resource_type=resource_type,
        resource_id=rid,
        summary=summary[:300],
        params=params or {},
        status="pending",
        request_ip=principal.ip,
        expires_at=utcnow() + TTL,
    )
    db.add(req)
    db.flush()
    audit(
        db,
        "approval.requested",
        principal=principal,
        resource_type=resource_type,
        resource_id=rid,
        metadata={"approval_id": str(req.id), "action": action},
    )
    notify(
        db,
        principal.user.id,
        "approval_requested",
        f"Approval needed: {DESTRUCTIVE[action].lower()}",
        f"API key “{principal.api_key.name}” asked to {summary[:200]}. Nothing happens until you approve it.",
        severity="warning",
        link=f"/approvals?id={req.id}",
        dedupe_key=f"approval:{req.id}",
    )
    body = to_out(req, principal.api_key.name)
    # The request must survive the non-2xx-style exit path (the request session rolls back on
    # exceptions), so it is committed here. Nothing else has been written by the caller yet.
    db.commit()
    raise ApprovalRequired(req, body)


def get_for(db: Session, principal: Principal, approval_id: uuid.UUID) -> ApprovalRequest:
    r = db.scalar(
        select(ApprovalRequest).where(ApprovalRequest.id == approval_id, ApprovalRequest.owner_id == principal.user.id)
    )
    if r is None or (principal.api_key is not None and r.api_key_id != principal.api_key.id):
        raise NotFound("Approval request not found.")
    expire_if_due(r)
    return r


def expire_if_due(r: ApprovalRequest) -> None:
    if r.status == "pending" and r.expires_at <= utcnow():
        r.status = "expired"
        r.decided_at = r.expires_at


def reject(db: Session, principal: Principal, r: ApprovalRequest, reason: str | None) -> None:
    if r.status != "pending":
        raise Conflict(f"This request is {r.status}.", code="invalid_state")
    r.status = "rejected"
    r.decided_at = utcnow()
    r.decided_by = principal.user.id
    r.decision_reason = reason
    audit(
        db,
        "approval.rejected",
        principal=principal,
        resource_type=r.resource_type,
        resource_id=r.resource_id,
        metadata={"approval_id": str(r.id), "action": r.action},
        reason=reason,
    )


def approve(db: Session, principal: Principal, r: ApprovalRequest, reason: str | None) -> dict[str, Any]:
    """Execute the approved operation as the approving user (normal checks apply)."""
    if principal.api_key is not None:
        raise ApiError("API keys can't approve requests.", code="forbidden", status_code=403)
    if r.status != "pending":
        raise Conflict(f"This request is {r.status}.", code="invalid_state")
    r.status = "approved"
    r.decided_at = utcnow()
    r.decided_by = principal.user.id
    r.decision_reason = reason
    audit(
        db,
        "approval.approved",
        principal=principal,
        resource_type=r.resource_type,
        resource_id=r.resource_id,
        metadata={"approval_id": str(r.id), "action": r.action, "api_key_id": str(r.api_key_id)},
        reason=reason,
    )
    try:
        with db.begin_nested():  # a failed execution leaves nothing half-done
            result = _execute(db, principal, r)
    except ApiError as e:
        r.status = "failed"
        r.result = {"error": e.code, "message": str(e)[:300]}
        return r.result
    r.status = "executed"
    r.result = result
    return result


def _execute(db: Session, principal: Principal, r: ApprovalRequest) -> dict[str, Any]:
    from app.services import backup_service
    from app.services import database_service as svc

    rid = uuid.UUID(r.resource_id)
    reason = f"approved request {r.id}"
    if r.action == "database.delete":
        d = svc.get_database_for(db, principal, rid, lock=True)
        svc.request_delete(db, principal, d, reason=reason, final_backup=bool(r.params.get("final_backup")))
        return {"status": "deleting", "database_id": str(d.id)}
    if r.action == "database.credentials_rotate":
        from app.services import plans

        d = svc.get_database_for(db, principal, rid, lock=True)
        if not plans.entitlements_for(db, principal.user.id).get("credential_rotation", False):
            raise ApiError("Credential rotation isn't available on your plan.", code="feature_unavailable")
        svc.request_rotation(db, principal, d)
        return {"status": "rotating", "database_id": str(d.id)}
    if r.action == "project.delete":
        from app.api.v1.projects import delete_project_record

        p = db.scalar(select(Project).where(Project.id == rid, Project.owner_id == principal.user.id))
        if p is None:
            raise NotFound("Project not found.")
        delete_project_record(db, principal, p)
        return {"status": "deleted", "project_id": str(p.id)}
    if r.action == "backup.delete":
        b, _d = backup_service.get_backup_for(db, principal, rid)
        backup_service.delete_backup(db, principal, b)
        return {"status": "deleted", "backup_id": str(b.id)}
    raise ApiError("Unsupported action.", code="invalid_request")

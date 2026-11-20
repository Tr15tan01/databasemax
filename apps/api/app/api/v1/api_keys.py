from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import ApiError, NotFound, QuotaExceeded
from app.core.ratelimit import hit
from app.models import ApiKey, Project, utcnow
from app.schemas.common import ApiModel, Message, OutModel
from app.security.api_keys import SCOPES, generate_api_key, hash_api_key
from app.security.deps import Principal, require_recent_auth, require_session_user
from app.services import plans
from app.services.audit import audit

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


class ApiKeyCreate(ApiModel):
    name: str = Field(min_length=1, max_length=64)
    kind: Literal["standard", "agent"] = Field(
        "standard",
        description="`agent` marks a machine identity (e.g. an AI agent or automation). Agent keys "
        "always need human approval for destructive operations.",
    )
    requires_approval: bool = Field(
        False, description="Require human approval for destructive operations (always on for agent keys)"
    )
    description: str | None = Field(None, max_length=300)
    scopes: list[str] = Field(min_length=1, max_length=len(SCOPES))
    project_ids: list[uuid.UUID] | None = Field(None, max_length=50)
    expires_in_days: int | None = Field(None, ge=1, le=365)

    @field_validator("scopes")
    @classmethod
    def _scopes(cls, v: list[str]) -> list[str]:
        unknown = set(v) - set(SCOPES)
        if unknown:
            raise ValueError(f"unknown scopes: {sorted(unknown)}")
        return sorted(set(v))


class ApiKeyOut(OutModel):
    id: uuid.UUID
    name: str
    kind: str
    requires_approval: bool
    description: str | None
    prefix: str
    scopes: list[str]
    project_ids: list[uuid.UUID] | None
    created_at: dt.datetime
    last_used_at: dt.datetime | None
    expires_at: dt.datetime | None
    revoked_at: dt.datetime | None
    status: str


class ApiKeyCreated(ApiKeyOut):
    key: str


def _out(k: ApiKey) -> ApiKeyOut:
    status = "revoked" if k.revoked_at else ("expired" if k.expires_at and k.expires_at <= utcnow() else "active")
    return ApiKeyOut(
        id=k.id,
        name=k.name,
        kind=k.kind,
        requires_approval=k.kind == "agent" or k.requires_approval,
        description=k.description,
        prefix=k.prefix,
        scopes=k.scopes,
        project_ids=k.project_ids,
        created_at=k.created_at,
        last_used_at=k.last_used_at,
        expires_at=k.expires_at,
        revoked_at=k.revoked_at,
        status=status,
    )


@router.get("/scopes", summary="Available API key scopes")
def list_scopes() -> list[dict[str, str]]:
    return [{"scope": k, "description": v} for k, v in SCOPES.items()]


@router.get("", response_model=list[ApiKeyOut], summary="List API keys")
def list_keys(
    db: Session = Depends(get_db, scope="function"), principal: Principal = Depends(require_session_user)
) -> list[ApiKeyOut]:
    rows = db.scalars(select(ApiKey).where(ApiKey.user_id == principal.user.id).order_by(ApiKey.created_at.desc()))
    return [_out(k) for k in rows]


@router.post(
    "", status_code=201, response_model=ApiKeyCreated, summary="Create an API key (the secret is shown only once)"
)
def create_key(
    body: ApiKeyCreate,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_session_user),
) -> ApiKeyCreated:
    # API keys can only be created from a dashboard session: a key can never mint more keys.
    hit("apikey-create", str(principal.user.id), 20, 3600)
    ent = plans.entitlements_for(db, principal.user.id)
    if not ent.get("api_access", True):
        raise ApiError("API access isn't available on your plan.", code="feature_unavailable", status_code=402)
    active = (
        db.scalar(select(func.count(ApiKey.id)).where(ApiKey.user_id == principal.user.id, ApiKey.revoked_at.is_(None)))
        or 0
    )
    if active >= ent.get("max_api_keys", 5):
        raise QuotaExceeded(f"Your plan allows {ent.get('max_api_keys', 5)} active API keys. Revoke one first.")
    if body.project_ids:
        owned = set(
            db.scalars(
                select(Project.id).where(
                    Project.owner_id == principal.user.id,
                    Project.id.in_(body.project_ids),
                    Project.deleted_at.is_(None),
                )
            )
        )
        if owned != set(body.project_ids):
            raise NotFound("One or more projects were not found.")
    key, prefix = generate_api_key()
    k = ApiKey(
        id=uuid.uuid4(),
        user_id=principal.user.id,
        name=body.name.strip(),
        kind=body.kind,
        requires_approval=body.kind == "agent" or body.requires_approval,
        description=(body.description or "").strip() or None,
        prefix=prefix,
        key_hash=hash_api_key(key),
        scopes=body.scopes,
        project_ids=body.project_ids or None,
        expires_at=utcnow() + dt.timedelta(days=body.expires_in_days) if body.expires_in_days else None,
    )
    db.add(k)
    db.flush()
    audit(
        db,
        "api_key.create",
        principal=principal,
        resource_type="api_key",
        resource_id=k.id,
        metadata={
            "name": k.name,
            "kind": k.kind,
            "requires_approval": k.requires_approval,
            "scopes": k.scopes,
            "project_restricted": bool(k.project_ids),
        },
    )
    return ApiKeyCreated(**_out(k).model_dump(), key=key)


@router.delete("/{key_id}", response_model=Message, summary="Revoke an API key")
def revoke_key(
    key_id: uuid.UUID,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_session_user),
) -> Message:
    k = db.scalar(select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == principal.user.id))
    if k is None:
        raise NotFound("API key not found.")
    if k.revoked_at is None:
        k.revoked_at = utcnow()
        audit(
            db,
            "api_key.revoke",
            principal=principal,
            resource_type="api_key",
            resource_id=k.id,
            metadata={"name": k.name},
        )
    return Message(status="revoked")


class ApiKeyUpdate(ApiModel):
    requires_approval: bool | None = None
    description: str | None = Field(None, max_length=300)


@router.patch("/{key_id}", response_model=ApiKeyOut, summary="Update an API key's approval setting or description")
def update_key(
    key_id: uuid.UUID,
    body: ApiKeyUpdate,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_session_user),
) -> ApiKeyOut:
    k = db.scalar(select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == principal.user.id))
    if k is None or k.revoked_at is not None:
        raise NotFound("API key not found.")
    changes: dict[str, str] = {}
    if body.requires_approval is not None and body.requires_approval != k.requires_approval:
        if k.kind == "agent" and not body.requires_approval:
            raise ApiError(
                "Agent keys always require approval for destructive operations.",
                code="invalid_request",
                status_code=400,
            )
        if not body.requires_approval:
            require_recent_auth(principal)  # loosening a safeguard needs a fresh sign-in
        k.requires_approval = body.requires_approval
        changes["requires_approval"] = str(body.requires_approval).lower()
    if body.description is not None:
        k.description = body.description.strip() or None
        changes["description"] = "updated"
    if changes:
        audit(
            db,
            "api_key.update",
            principal=principal,
            resource_type="api_key",
            resource_id=k.id,
            metadata={"name": k.name, **changes},
        )
    return _out(k)


class KeyActivityOut(OutModel):
    key: ApiKeyOut
    requests_24h: int
    requests_7d: int
    hourly: list[int]  # last 24 hours, oldest first
    actions_7d: int
    failed_actions_7d: int
    pending_approvals: int
    last_action: str | None
    last_action_at: dt.datetime | None


@router.get(
    "/activity", response_model=list[KeyActivityOut], summary="Per-key usage: requests, actions, pending approvals"
)
def key_activity(
    kind: Literal["standard", "agent"] | None = None,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_session_user),
) -> list[KeyActivityOut]:
    from app.models import ApiKeyRequestCounter, ApprovalRequest, AuditEvent

    now = utcnow()
    hour0 = now.replace(minute=0, second=0, microsecond=0)
    stmt = select(ApiKey).where(ApiKey.user_id == principal.user.id)
    if kind:
        stmt = stmt.where(ApiKey.kind == kind)
    keys = list(db.scalars(stmt.order_by(ApiKey.revoked_at.is_not(None), ApiKey.created_at.desc())))
    if not keys:
        return []
    ids = [k.id for k in keys]
    counts: dict[uuid.UUID, dict[dt.datetime, int]] = {}
    for kid, hour, cnt in db.execute(
        select(ApiKeyRequestCounter.api_key_id, ApiKeyRequestCounter.hour, ApiKeyRequestCounter.count).where(
            ApiKeyRequestCounter.api_key_id.in_(ids), ApiKeyRequestCounter.hour >= hour0 - dt.timedelta(days=7)
        )
    ):
        counts.setdefault(kid, {})[hour] = int(cnt)
    week = now - dt.timedelta(days=7)
    actions = {
        r[0]: (int(r[1]), int(r[2]))
        for r in db.execute(
            select(
                AuditEvent.actor_api_key_id,
                func.count(AuditEvent.id),
                func.count(AuditEvent.id).filter(AuditEvent.outcome != "success"),
            )
            .where(
                AuditEvent.tenant_user_id == principal.user.id,
                AuditEvent.actor_api_key_id.in_(ids),
                AuditEvent.created_at >= week,
            )
            .group_by(AuditEvent.actor_api_key_id)
        )
    }
    last: dict[uuid.UUID, tuple[str, dt.datetime]] = {}
    for key_ref, action, at in db.execute(
        select(AuditEvent.actor_api_key_id, AuditEvent.action, AuditEvent.created_at)
        .distinct(AuditEvent.actor_api_key_id)
        .where(AuditEvent.tenant_user_id == principal.user.id, AuditEvent.actor_api_key_id.in_(ids))
        .order_by(AuditEvent.actor_api_key_id, AuditEvent.created_at.desc())
    ):
        if key_ref is not None:
            last[key_ref] = (action, at)
    pending = dict(
        db.execute(
            select(ApprovalRequest.api_key_id, func.count(ApprovalRequest.id))
            .where(
                ApprovalRequest.owner_id == principal.user.id,
                ApprovalRequest.api_key_id.in_(ids),
                ApprovalRequest.status == "pending",
                ApprovalRequest.expires_at > now,
            )
            .group_by(ApprovalRequest.api_key_id)
        ).all()
    )
    out = []
    for k in keys:
        c = counts.get(k.id, {})
        hourly = [c.get(hour0 - dt.timedelta(hours=23 - i), 0) for i in range(24)]
        a = actions.get(k.id, (0, 0))
        la = last.get(k.id)
        out.append(
            KeyActivityOut(
                key=_out(k),
                requests_24h=sum(hourly),
                requests_7d=sum(c.values()),
                hourly=hourly,
                actions_7d=a[0],
                failed_actions_7d=a[1],
                pending_approvals=int(pending.get(k.id, 0)),
                last_action=la[0] if la else None,
                last_action_at=la[1] if la else None,
            )
        )
    return out

"""Audit trail writer. Metadata is filtered so secrets can never be recorded."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from app.core.logging import request_id_var
from app.models import AuditEvent

if TYPE_CHECKING:
    from app.security.deps import Principal

_FORBIDDEN_META_KEYS = ("password", "secret", "token", "key_hash", "verifier", "connection_url", "url")


def _clean(meta: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in (meta or {}).items():
        if any(f in k.lower() for f in _FORBIDDEN_META_KEYS):
            continue
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v if not isinstance(v, str) else v[:300]
        elif isinstance(v, (list, tuple)):
            out[k] = [str(x)[:100] for x in v][:50]
        else:
            out[k] = str(v)[:300]
    return out


def audit(
    db: Session,
    action: str,
    *,
    principal: Principal | None = None,
    actor_type: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    tenant_user_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: uuid.UUID | str | None = None,
    outcome: str = "success",
    reason: str | None = None,
    ip: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditEvent:
    api_key_id = None
    if principal is not None:
        actor_type = actor_type or principal.actor_type
        actor_user_id = actor_user_id or principal.user.id
        api_key_id = principal.api_key.id if principal.api_key else None
        ip = ip or principal.ip
        if tenant_user_id is None:
            tenant_user_id = principal.user.id
    ev = AuditEvent(
        id=uuid.uuid4(),
        tenant_user_id=tenant_user_id,
        actor_user_id=actor_user_id,
        actor_type=actor_type or "system",
        actor_api_key_id=api_key_id,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id else None,
        outcome=outcome,
        reason=(reason or None) and reason[:500],
        request_id=request_id_var.get(),
        ip=ip,
        metadata_=_clean(metadata),
    )
    db.add(ev)
    return ev

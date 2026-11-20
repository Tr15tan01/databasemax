from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.auth import build_me
from app.core.db import get_db, set_rls
from app.core.errors import ApiError
from app.models import Database, Project, utcnow
from app.schemas.auth import AccountUpdateIn, DeleteAccountIn, MeOut
from app.schemas.common import Message
from app.security.deps import Principal, require_recent_auth, require_session_user
from app.security.sessions import clear_session_cookies, revoke_all_sessions
from app.services import database_service
from app.services.audit import audit
from app.services.auth_service import revoke_api_keys
from app.services.notifications import NOTIFICATION_TYPES

router = APIRouter(prefix="/account", tags=["account"])


@router.patch("", response_model=MeOut, summary="Update profile and preferences")
def update_account(
    body: AccountUpdateIn,
    principal: Principal = Depends(require_session_user),
    db: Session = Depends(get_db, scope="function"),
) -> MeOut:
    set_rls(db, system=True)
    user = principal.user
    if body.display_name is not None:
        user.display_name = body.display_name.strip() or None
    if body.preferences is not None:
        prefs = dict(user.preferences or {})
        for k, v in body.preferences.items():
            if k == "email_notifications":
                unknown = set(v) - set(NOTIFICATION_TYPES)
                if unknown:
                    raise ApiError(
                        f"Unknown notification types: {sorted(unknown)}", code="validation_error", status_code=422
                    )
                # Essential notifications can't be disabled.
                v = {t: val for t, val in v.items() if not NOTIFICATION_TYPES[t][0]}
                prefs[k] = {**prefs.get(k, {}), **v}
            else:
                prefs[k] = v
        user.preferences = prefs
    user.updated_at = utcnow()
    audit(db, "account.update", principal=principal, resource_type="user", resource_id=user.id)
    return build_me(db, principal)


@router.get("/notification-settings", summary="Email notification settings")
def notification_settings(principal: Principal = Depends(require_session_user)) -> list[dict[str, object]]:
    from app.services.notifications import email_enabled_for

    return [
        {"type": t, "essential": ess, "email": email_enabled_for(principal.user, t)}
        for t, (ess, _default) in NOTIFICATION_TYPES.items()
    ]


@router.delete("", response_model=Message, summary="Delete your account and all databases")
def delete_account(
    body: DeleteAccountIn,
    response: Response,
    principal: Principal = Depends(require_session_user),
    db: Session = Depends(get_db, scope="function"),
) -> Message:
    user = principal.user
    if body.confirm.strip().lower() != user.email.lower():
        raise ApiError("Type your email address to confirm.", code="confirmation_mismatch")
    require_recent_auth(principal)
    # Schedule deletion of every database (the worker destroys instances and credentials).
    for d in db.scalars(
        select(Database).where(Database.owner_id == user.id, Database.status.notin_(("deleted", "deleting")))
    ):
        database_service.request_delete(db, principal, d, reason="account deleted")
    for p in db.scalars(select(Project).where(Project.owner_id == user.id, Project.deleted_at.is_(None))):
        p.status = "deleted"
        p.deleted_at = utcnow()
    set_rls(db, system=True)
    revoke_api_keys(db, user.id)
    revoke_all_sessions(db, user.id, "account_deleted")
    audit(db, "account.delete", principal=principal, resource_type="user", resource_id=user.id)
    user.status = "deleted"
    user.deleted_at = utcnow()
    user.email = f"deleted+{user.id}@deleted.invalid"
    user.password_hash = None
    user.display_name = None
    user.preferences = {}
    for oa in list(user.oauth_accounts):
        db.delete(oa)
    clear_session_cookies(response)
    from app.core.redis import wake_workers

    wake_workers()
    return Message(status="deleted", message="Your account and databases are being deleted.")

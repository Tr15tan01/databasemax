"""Authentication and authorization dependencies.

Two kinds of principals:
- human user via browser session cookie (`actor_type="user"`), all scopes, subject to CSRF;
- machine identity via API key (`actor_type="api_key"`), limited to the key's scopes and,
  optionally, to a list of projects.

Every dependency that authenticates also narrows the request's DB session to the tenant
(Row-Level Security context).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.core.config import get_settings
from app.core.db import get_db, set_rls
from app.core.errors import ApiError, Forbidden, Unauthorized
from app.models import AdminRoleAssignment, ApiKey, Session, User, utcnow
from app.security.api_keys import SCOPES, hash_api_key, parse_prefix
from app.security.sessions import lookup_session


@dataclass
class Principal:
    user: User
    actor_type: str  # "user" | "api_key"
    session: Session | None = None
    api_key: ApiKey | None = None
    scopes: frozenset[str] = field(default_factory=frozenset)
    project_ids: frozenset[uuid.UUID] | None = None
    ip: str | None = None
    is_admin: bool = False

    def has_scope(self, scope: str) -> bool:
        return self.actor_type == "user" or scope in self.scopes

    def can_access_project(self, project_id: uuid.UUID) -> bool:
        return self.project_ids is None or project_id in self.project_ids


def client_ip(request: Request) -> str | None:
    """Client address as seen by uvicorn (behind Caddy, uvicorn's --proxy-headers with a
    restricted --forwarded-allow-ips supplies the real client IP). Non-IP values are dropped."""
    import ipaddress

    host = request.client.host if request.client else None
    if not host:
        return None
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


def _is_admin(db: DbSession, user_id: uuid.UUID) -> bool:
    return (
        db.scalar(
            select(AdminRoleAssignment.id).where(
                AdminRoleAssignment.user_id == user_id,
                AdminRoleAssignment.role == "admin",
                AdminRoleAssignment.revoked_at.is_(None),
            )
        )
        is not None
    )


def _authenticate(request: Request, db: DbSession) -> Principal | None:
    auth = request.headers.get("authorization", "")
    ip = client_ip(request)
    # Identity lookups run with RLS bypass (sessions/users/api_keys by hash), then the request
    # session is narrowed to the resolved tenant.
    set_rls(db, system=True)
    try:
        if auth:
            scheme, _, token = auth.partition(" ")
            if scheme.lower() != "bearer" or not token:
                raise Unauthorized("Invalid authorization header.")
            prefix = parse_prefix(token.strip())
            if prefix is None:
                raise Unauthorized("Invalid API key.")
            key = db.scalar(select(ApiKey).where(ApiKey.prefix == prefix))
            if (
                key is None
                or key.revoked_at is not None
                or (key.expires_at is not None and key.expires_at <= utcnow())
                or not _ct_eq(key.key_hash, hash_api_key(token.strip()))
            ):
                raise Unauthorized("Invalid API key.")
            user = db.get(User, key.user_id)
            if user is None or user.status != "active":
                raise Unauthorized("Invalid API key.")
            now = utcnow()
            if key.last_used_at is None or now - key.last_used_at > dt.timedelta(minutes=1):
                key.last_used_at = now
                key.last_used_ip = ip
            return Principal(
                user=user,
                actor_type="api_key",
                api_key=key,
                scopes=frozenset(key.scopes),
                project_ids=frozenset(key.project_ids) if key.project_ids else None,
                ip=ip,
            )
        cookie_token = request.cookies.get(get_settings().session_cookie_name)
        if not cookie_token:
            return None
        sess = lookup_session(db, cookie_token)
        if sess is None:
            return None
        user = db.get(User, sess.user_id)
        if user is None or user.status != "active":
            return None
        return Principal(
            user=user,
            actor_type="user",
            session=sess,
            scopes=frozenset(SCOPES),
            ip=ip,
            is_admin=_is_admin(db, user.id),
        )
    finally:
        set_rls(db, system=False)


def _count_request(user_id: uuid.UUID, api_key_id: uuid.UUID | None = None) -> None:
    """Best-effort hourly API request counter (flushed to PostgreSQL by the worker). Requests
    made with an API key are also counted per key as `k:<key id>:<user id>`."""
    import redis

    from app.core.redis import get_redis

    try:
        key = f"dmx:apicount:{utcnow():%Y%m%d%H}"
        r = get_redis()
        pipe = r.pipeline()
        pipe.hincrby(key, str(user_id), 1)
        if api_key_id is not None:
            pipe.hincrby(key, f"k:{api_key_id}:{user_id}", 1)
        pipe.expire(key, 7 * 24 * 3600)
        pipe.execute()
    except redis.RedisError:
        pass


def _ct_eq(a: bytes, b: bytes) -> bool:
    import hmac

    return hmac.compare_digest(a, b)


WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _enforce_api_rate(request: Request, principal: Principal) -> None:
    """Per-credential request budgets: each API key has its own; browser sessions share the
    user's; state-changing requests have a tighter per-user budget."""
    from app.core.ratelimit import hit

    s = get_settings()
    if principal.api_key is not None:
        hit("api-key", str(principal.api_key.id), s.api_rate_per_minute_api_key, 60, fail_closed=False)
    else:
        hit("api-user", str(principal.user.id), s.api_rate_per_minute_user, 60, fail_closed=False)
    if request.method in WRITE_METHODS:
        hit("api-write", str(principal.user.id), s.api_write_rate_per_minute, 60, fail_closed=False)


def get_principal_optional(request: Request, db: DbSession = Depends(get_db, scope="function")) -> Principal | None:
    principal = _authenticate(request, db)
    if principal is not None:
        _enforce_api_rate(request, principal)
        _count_request(principal.user.id, principal.api_key.id if principal.api_key else None)
        set_rls(db, user_id=principal.user.id)
        request.state.user_id = str(principal.user.id)
        request.state.principal = principal
    return principal


def require_principal(principal: Principal | None = Depends(get_principal_optional)) -> Principal:
    if principal is None:
        raise Unauthorized("Authentication required.")
    return principal


def require_session_user(principal: Principal = Depends(require_principal)) -> Principal:
    """Account-management endpoints are available to the human user only (not API keys)."""
    if principal.actor_type != "user":
        raise Forbidden("This action requires signing in to the dashboard.", code="session_required")
    return principal


def require_scope(scope: str) -> Callable[..., Principal]:
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope}")

    def dep(principal: Principal = Depends(require_principal)) -> Principal:
        if not principal.has_scope(scope):
            raise Forbidden(f"API key is missing the '{scope}' scope.", code="insufficient_scope")
        return principal

    return dep


def require_verified(principal: Principal) -> None:
    if not principal.user.is_verified:
        raise ApiError(
            "Verify your email address before performing this action.", code="email_not_verified", status_code=403
        )


def require_admin(
    principal: Principal = Depends(require_session_user), db: DbSession = Depends(get_db, scope="function")
) -> Principal:
    if not principal.is_admin:
        # 404 rather than 403 so the admin surface isn't advertised to ordinary users.
        raise ApiError("Not found.", code="not_found", status_code=404)
    set_rls(db, system=True)
    return principal


def require_recent_auth(principal: Principal) -> None:
    s = get_settings()
    sess = principal.session
    if sess is None:
        raise Forbidden("This action requires a dashboard session.", code="session_required")
    at = sess.reauthenticated_at or sess.created_at
    window = dt.timedelta(minutes=s.reauth_window_minutes)
    if not principal.user.password_hash:
        # Accounts without a password (Google sign-in) re-authenticate by signing in again.
        window = dt.timedelta(hours=12)
    if utcnow() - at > window:
        raise ApiError("Please confirm your password to continue.", code="reauthentication_required", status_code=403)

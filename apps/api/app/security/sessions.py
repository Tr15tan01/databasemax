"""Server-side browser sessions."""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session as DbSession

from app.core.config import get_settings
from app.models import Session, User, utcnow
from app.security.csrf import new_csrf_token
from app.security.tokens import hash_token, new_token

TOUCH_INTERVAL = dt.timedelta(minutes=5)


def create_session(
    db: DbSession, user: User, *, method: str, ip: str | None, user_agent: str | None
) -> tuple[Session, str]:
    s = get_settings()
    token = new_token(32)
    now = utcnow()
    sess = Session(
        id=uuid.uuid4(),
        user_id=user.id,
        token_hash=hash_token(token),
        auth_method=method,
        created_at=now,
        last_seen_at=now,
        reauthenticated_at=now,
        expires_at=now + dt.timedelta(hours=s.session_ttl_hours),
        ip=ip,
        user_agent=(user_agent or "")[:400] or None,
    )
    db.add(sess)
    db.flush()
    return sess, token


def lookup_session(db: DbSession, token: str) -> Session | None:
    if not token or len(token) > 128:
        return None
    s = get_settings()
    sess = db.scalar(select(Session).where(Session.token_hash == hash_token(token)))
    if sess is None or sess.revoked_at is not None:
        return None
    now = utcnow()
    if sess.expires_at <= now:
        return None
    if sess.last_seen_at + dt.timedelta(hours=s.session_idle_timeout_hours) <= now:
        return None
    if now - sess.last_seen_at > TOUCH_INTERVAL:
        sess.last_seen_at = now
    return sess


def revoke_session(db: DbSession, sess: Session, reason: str) -> None:
    if sess.revoked_at is None:
        sess.revoked_at = utcnow()
        sess.revoked_reason = reason


def revoke_all_sessions(db: DbSession, user_id: uuid.UUID, reason: str, *, except_id: uuid.UUID | None = None) -> int:
    stmt = (
        update(Session)
        .where(Session.user_id == user_id, Session.revoked_at.is_(None))
        .values(revoked_at=utcnow(), revoked_reason=reason)
    )
    if except_id is not None:
        stmt = stmt.where(Session.id != except_id)
    result = db.execute(stmt)
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


def set_session_cookies(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        s.session_cookie_name,
        token,
        max_age=s.session_ttl_hours * 3600,
        httponly=True,
        secure=s.cookie_secure,
        samesite="lax",
        domain=s.cookie_domain,
        path="/",
    )
    set_csrf_cookie(response)


def set_csrf_cookie(response: Response, token: str | None = None) -> str:
    s = get_settings()
    token = token or new_csrf_token()
    response.set_cookie(
        s.csrf_cookie_name,
        token,
        max_age=s.session_ttl_hours * 3600,
        httponly=False,  # must be readable by the web app to echo in X-CSRF-Token
        secure=s.cookie_secure,
        samesite="lax",
        domain=s.cookie_domain,
        path="/",
    )
    return token


def clear_session_cookies(response: Response) -> None:
    s = get_settings()
    response.delete_cookie(s.session_cookie_name, domain=s.cookie_domain, path="/")

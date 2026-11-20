"""Account lifecycle: registration, verification, login, password reset/change, deletion."""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session as DbSession

from app.core.config import get_settings
from app.core.errors import ApiError, Unauthorized
from app.core.logging import log_event
from app.core.ratelimit import (
    login_block_seconds,
    record_login_failure,
    reset_login_failures,
)
from app.models import ApiKey, EmailToken, LoginAttempt, Subscription, User, utcnow
from app.security.passwords import (
    dummy_verify,
    hash_password,
    needs_rehash,
    password_problems,
    verify_password,
)
from app.security.tokens import hash_token, new_token
from app.services.audit import audit
from app.services.email import enqueue_email

log = logging.getLogger("dmx.auth")

GENERIC_LOGIN_ERROR = "Invalid email or password."


def find_user_by_email(db: DbSession, email: str) -> User | None:
    return db.scalar(select(User).where(func.lower(User.email) == email.lower(), User.deleted_at.is_(None)))


def ensure_subscription(db: DbSession, user: User) -> Subscription:
    sub = db.scalar(select(Subscription).where(Subscription.user_id == user.id))
    if sub is None:
        sub = Subscription(id=uuid.uuid4(), user_id=user.id, plan="free", status="active")
        db.add(sub)
        db.flush()
    return sub


def check_password_policy(password: str, email: str | None) -> None:
    problems = password_problems(password, email)
    if problems:
        raise ApiError(problems[0], code="weak_password", status_code=422, details=problems)


def _issue_email_token(db: DbSession, user: User, purpose: str, ttl: dt.timedelta) -> str:
    # Invalidate previous unused tokens of the same purpose (only the newest link works).
    db.execute(
        update(EmailToken)
        .where(EmailToken.user_id == user.id, EmailToken.purpose == purpose, EmailToken.used_at.is_(None))
        .values(used_at=utcnow())
    )
    token = new_token(32)
    db.add(
        EmailToken(
            id=uuid.uuid4(),
            user_id=user.id,
            purpose=purpose,
            token_hash=hash_token(token),
            expires_at=utcnow() + ttl,
        )
    )
    return token


def send_verification(db: DbSession, user: User) -> None:
    s = get_settings()
    token = _issue_email_token(db, user, "verify_email", dt.timedelta(hours=s.email_verification_ttl_hours))
    url = f"{s.public_web_url.rstrip('/')}/verify-email#token={token}"
    enqueue_email(
        db,
        to=user.email,
        template="verify_email",
        user_id=user.id,
        context={"url": url, "hours": s.email_verification_ttl_hours},
    )


def register(
    db: DbSession,
    *,
    email: str,
    password: str,
    display_name: str | None,
    ip: str | None,
    pre_verified: bool = False,
    source: str = "web",
) -> User | None:
    """Returns the new user, or None if the email is already registered (the caller responds
    identically in both cases to avoid account enumeration).

    `pre_verified` is only for operator-created accounts (CLI): the email is marked verified and
    no verification email is sent. The public registration endpoint never sets it."""
    check_password_policy(password, email)
    existing = find_user_by_email(db, email)
    if existing is not None and source != "web":
        return None
    if existing is not None:
        s = get_settings()
        enqueue_email(
            db,
            to=existing.email,
            template="notification",
            user_id=existing.id,
            context={
                "title": "Someone tried to create an account with your email",
                "body": "An account already exists for this address. If this was you, sign in or reset your "
                "password. Otherwise you can ignore this message.",
                "url": f"{s.public_web_url.rstrip('/')}/login",
            },
            dedupe_key=f"dup-register:{existing.id}:{utcnow():%Y%m%d%H}",
        )
        audit(
            db,
            "auth.register_duplicate",
            actor_type="system",
            tenant_user_id=existing.id,
            resource_type="user",
            resource_id=existing.id,
            ip=ip,
        )
        return None
    user = User(
        id=uuid.uuid4(),
        email=email,
        password_hash=hash_password(password),
        display_name=(display_name or "").strip() or None,
        status="active",
        password_changed_at=utcnow(),
    )
    if pre_verified:
        user.email_verified_at = utcnow()
    db.add(user)
    db.flush()
    ensure_subscription(db, user)
    if not pre_verified:
        send_verification(db, user)
    audit(
        db,
        "auth.register",
        actor_type="user" if source == "web" else "system",
        actor_user_id=user.id if source == "web" else None,
        tenant_user_id=user.id,
        resource_type="user",
        resource_id=user.id,
        ip=ip,
        metadata={"source": source, "pre_verified": pre_verified},
    )
    return user


def mark_email_verified(db: DbSession, user: User) -> bool:
    """Operator override (CLI / admin console). Returns False if already verified. The caller
    audits the action with its own actor and reason."""
    if user.email_verified_at is not None:
        return False
    user.email_verified_at = utcnow()
    db.execute(
        update(EmailToken)
        .where(EmailToken.user_id == user.id, EmailToken.purpose == "verify_email", EmailToken.used_at.is_(None))
        .values(used_at=utcnow())
    )
    return True


def verify_email(db: DbSession, token: str) -> User:
    row = db.scalar(
        select(EmailToken)
        .where(EmailToken.token_hash == hash_token(token), EmailToken.purpose == "verify_email")
        .with_for_update()
    )
    if row is None or row.used_at is not None or row.expires_at <= utcnow():
        raise ApiError("This verification link is invalid or has expired.", code="invalid_token", status_code=400)
    user = db.get(User, row.user_id)
    if user is None or user.status != "active":
        raise ApiError("This verification link is invalid or has expired.", code="invalid_token", status_code=400)
    row.used_at = utcnow()
    if user.email_verified_at is None:
        user.email_verified_at = utcnow()
    audit(
        db,
        "auth.email_verified",
        actor_type="user",
        actor_user_id=user.id,
        tenant_user_id=user.id,
        resource_type="user",
        resource_id=user.id,
    )
    return user


def _record_attempt(
    db: DbSession,
    email: str,
    user: User | None,
    success: bool,
    reason: str | None,
    ip: str | None,
    ua: str | None,
    method: str = "password",
) -> None:
    db.add(
        LoginAttempt(
            id=uuid.uuid4(),
            user_id=user.id if user else None,
            email_hash=hashlib.sha256(email.lower().encode()).digest(),
            success=success,
            reason=reason,
            method=method,
            ip=ip,
            user_agent=(ua or "")[:400] or None,
        )
    )


def authenticate(db: DbSession, *, email: str, password: str, ip: str | None, ua: str | None) -> User:
    blocked = login_block_seconds(email)
    if blocked:
        dummy_verify()
        _record_attempt(db, email, None, False, "throttled", ip, ua)
        raise ApiError(
            "Too many failed sign-in attempts. Try again later.",
            code="rate_limited",
            status_code=429,
            headers={"Retry-After": str(blocked)},
        )
    user = find_user_by_email(db, email)
    ok = verify_password(user.password_hash if user else None, password)
    if not ok or user is None:
        record_login_failure(email)
        _record_attempt(db, email, user, False, "bad_credentials", ip, ua)
        if user is not None:
            audit(
                db,
                "auth.login_failed",
                actor_type="user",
                actor_user_id=user.id,
                tenant_user_id=user.id,
                resource_type="user",
                resource_id=user.id,
                outcome="failure",
                ip=ip,
            )
        log_event(log, "login failed", ip=ip)
        raise Unauthorized(GENERIC_LOGIN_ERROR, code="invalid_credentials")
    if user.status != "active":
        _record_attempt(db, email, user, False, f"status_{user.status}", ip, ua)
        raise ApiError("This account is suspended. Contact support.", code="account_suspended", status_code=403)
    reset_login_failures(email)
    if user.password_hash and needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    _record_attempt(db, email, user, True, None, ip, ua)
    return user


def after_login(db: DbSession, user: User, *, ip: str | None, method: str) -> None:
    """Security-sensitive login detection: a sign-in from an IP not seen in the last 90 days."""
    from app.services.notifications import notify

    user.last_login_at = utcnow()
    audit(
        db,
        "auth.login",
        actor_type="user",
        actor_user_id=user.id,
        tenant_user_id=user.id,
        resource_type="user",
        resource_id=user.id,
        ip=ip,
        metadata={"method": method},
    )
    if ip:
        seen = db.scalar(
            select(func.count(LoginAttempt.id)).where(
                LoginAttempt.user_id == user.id,
                LoginAttempt.success.is_(True),
                LoginAttempt.ip == ip,
                LoginAttempt.created_at > utcnow() - dt.timedelta(days=90),
            )
        )
        prior = db.scalar(
            select(func.count(LoginAttempt.id)).where(LoginAttempt.user_id == user.id, LoginAttempt.success.is_(True))
        )
        # `seen`/`prior` include the attempt just recorded for this login.
        if (prior or 0) > 1 and (seen or 0) <= 1:
            notify(
                db,
                user.id,
                "security_login",
                "New sign-in to your account",
                f"Your account was signed in from a new network ({ip}). If this wasn't you, change your "
                "password and revoke other sessions.",
                severity="warning",
                link="/settings/security",
                dedupe_key=f"login-ip:{ip}:{utcnow():%Y%m%d}",
            )


def request_password_reset(db: DbSession, email: str) -> None:
    user = find_user_by_email(db, email)
    if user is None or user.status != "active":
        dummy_verify()
        return
    s = get_settings()
    token = _issue_email_token(db, user, "reset_password", dt.timedelta(minutes=s.password_reset_ttl_minutes))
    url = f"{s.public_web_url.rstrip('/')}/reset-password#token={token}"
    enqueue_email(
        db,
        to=user.email,
        template="reset_password",
        user_id=user.id,
        context={"url": url, "minutes": s.password_reset_ttl_minutes},
    )
    audit(
        db,
        "auth.password_reset_requested",
        actor_type="user",
        actor_user_id=user.id,
        tenant_user_id=user.id,
        resource_type="user",
        resource_id=user.id,
    )


def reset_password(db: DbSession, token: str, new_password: str) -> User:
    from app.security.sessions import revoke_all_sessions

    row = db.scalar(
        select(EmailToken)
        .where(EmailToken.token_hash == hash_token(token), EmailToken.purpose == "reset_password")
        .with_for_update()
    )
    if row is None or row.used_at is not None or row.expires_at <= utcnow():
        raise ApiError("This reset link is invalid or has expired.", code="invalid_token", status_code=400)
    user = db.get(User, row.user_id)
    if user is None or user.status != "active":
        raise ApiError("This reset link is invalid or has expired.", code="invalid_token", status_code=400)
    check_password_policy(new_password, user.email)
    row.used_at = utcnow()
    user.password_hash = hash_password(new_password)
    user.password_changed_at = utcnow()
    # Resetting via email proves mailbox ownership.
    if user.email_verified_at is None:
        user.email_verified_at = utcnow()
    revoke_all_sessions(db, user.id, "password_reset")
    reset_login_failures(user.email)
    enqueue_email(db, to=user.email, template="password_changed", user_id=user.id, context={})
    audit(
        db,
        "auth.password_reset",
        actor_type="user",
        actor_user_id=user.id,
        tenant_user_id=user.id,
        resource_type="user",
        resource_id=user.id,
    )
    return user


def change_password(db: DbSession, user: User, current: str | None, new: str, *, keep_session: uuid.UUID) -> None:
    from app.security.sessions import revoke_all_sessions

    if user.password_hash and (not current or not verify_password(user.password_hash, current)):
        raise ApiError("Current password is incorrect.", code="invalid_password", status_code=400)
    check_password_policy(new, user.email)
    user.password_hash = hash_password(new)
    user.password_changed_at = utcnow()
    revoke_all_sessions(db, user.id, "password_changed", except_id=keep_session)
    enqueue_email(db, to=user.email, template="password_changed", user_id=user.id, context={})
    audit(
        db,
        "auth.password_changed",
        actor_type="user",
        actor_user_id=user.id,
        tenant_user_id=user.id,
        resource_type="user",
        resource_id=user.id,
    )


def revoke_api_keys(db: DbSession, user_id: uuid.UUID) -> None:
    db.execute(update(ApiKey).where(ApiKey.user_id == user_id, ApiKey.revoked_at.is_(None)).values(revoked_at=utcnow()))

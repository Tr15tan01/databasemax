from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.core.config import get_settings
from app.core.db import get_db, set_rls
from app.core.errors import ApiError, NotFound
from app.core.ratelimit import hit
from app.models import OAuthAccount, Plan, Session, Subscription, utcnow
from app.schemas.auth import (
    ChangePasswordIn,
    ForgotIn,
    LoginIn,
    MeOut,
    PlanSummary,
    ProvidersOut,
    ReauthIn,
    RegisterIn,
    ResetIn,
    SessionOut,
    TokenIn,
)
from app.schemas.common import Message
from app.security.deps import Principal, client_ip, require_principal, require_session_user
from app.security.passwords import verify_password
from app.security.sessions import (
    clear_session_cookies,
    create_session,
    revoke_all_sessions,
    revoke_session,
    set_csrf_cookie,
    set_session_cookies,
)
from app.security.tokens import hash_token, new_token
from app.services import auth_service, google_oauth
from app.services.audit import audit

router = APIRouter(prefix="/auth", tags=["auth"])


def build_me(db: DbSession, principal: Principal) -> MeOut:
    user = principal.user
    set_rls(db, user_id=user.id)
    sub = db.scalar(select(Subscription).where(Subscription.user_id == user.id))
    plan_id = sub.plan if sub else "free"
    plan = db.get(Plan, plan_id)
    providers = db.scalars(select(OAuthAccount.provider).where(OAuthAccount.user_id == user.id)).all()
    return MeOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        email_verified=user.is_verified,
        has_password=bool(user.password_hash),
        is_admin=principal.is_admin,
        created_at=user.created_at,
        preferences=user.preferences or {},
        plan=PlanSummary(id=plan_id, name=plan.name if plan else plan_id, status=sub.status if sub else "active"),
        auth_method=principal.session.auth_method if principal.session else "api_key",
        linked_providers=list(providers),
    )


@router.get("/csrf", summary="Issue a CSRF token cookie")
def csrf(response: Response) -> dict[str, str]:
    token = set_csrf_cookie(response)
    return {"csrf_token": token}


@router.get("/providers", response_model=ProvidersOut, summary="Available sign-in methods")
def providers() -> ProvidersOut:
    return ProvidersOut(password=True, google=get_settings().google_enabled)


@router.post("/register", status_code=202, response_model=Message, summary="Register with email and password")
def register(body: RegisterIn, request: Request, db: DbSession = Depends(get_db, scope="function")) -> Message:
    ip = client_ip(request)
    hit("register-ip", ip or "unknown", limit=10, window_seconds=3600)
    hit("register-email", body.email, limit=3, window_seconds=3600)
    set_rls(db, system=True)
    auth_service.register(db, email=body.email, password=body.password, display_name=body.display_name, ip=ip)
    # Identical response whether or not the email was already registered.
    return Message(status="verification_sent", message="Check your inbox to verify your email address.")


@router.post("/verify-email", response_model=MeOut, summary="Verify email with the emailed token (signs you in)")
def verify_email(
    body: TokenIn, request: Request, response: Response, db: DbSession = Depends(get_db, scope="function")
) -> MeOut:
    ip = client_ip(request)
    hit("verify-ip", ip or "unknown", limit=20, window_seconds=600)
    set_rls(db, system=True)
    user = auth_service.verify_email(db, body.token)
    sess, token = create_session(db, user, method="email_link", ip=ip, user_agent=request.headers.get("user-agent"))
    auth_service.after_login(db, user, ip=ip, method="email_link")
    set_session_cookies(response, token)
    from app.security.deps import _is_admin

    return build_me(db, Principal(user=user, actor_type="user", session=sess, is_admin=_is_admin(db, user.id)))


@router.post("/resend-verification", response_model=Message, summary="Resend the verification email")
def resend_verification(
    principal: Principal = Depends(require_session_user), db: DbSession = Depends(get_db, scope="function")
) -> Message:
    hit("resend-verify", str(principal.user.id), limit=3, window_seconds=3600)
    if principal.user.is_verified:
        return Message(status="already_verified")
    set_rls(db, system=True)
    auth_service.send_verification(db, principal.user)
    return Message(status="verification_sent")


@router.post("/login", response_model=MeOut, summary="Sign in with email and password")
def login(
    body: LoginIn, request: Request, response: Response, db: DbSession = Depends(get_db, scope="function")
) -> MeOut:
    ip = client_ip(request)
    hit("login-ip", ip or "unknown", limit=30, window_seconds=300)
    set_rls(db, system=True)
    try:
        user = auth_service.authenticate(
            db, email=body.email, password=body.password, ip=ip, ua=request.headers.get("user-agent")
        )
    except ApiError:
        db.commit()  # persist the failed-attempt records before responding
        raise
    sess, token = create_session(db, user, method="password", ip=ip, user_agent=request.headers.get("user-agent"))
    auth_service.after_login(db, user, ip=ip, method="password")
    set_session_cookies(response, token)
    from app.security.deps import _is_admin

    return build_me(db, Principal(user=user, actor_type="user", session=sess, is_admin=_is_admin(db, user.id)))


@router.post("/logout", response_model=Message, summary="Sign out (revokes the current session)")
def logout(
    response: Response,
    principal: Principal = Depends(require_session_user),
    db: DbSession = Depends(get_db, scope="function"),
) -> Message:
    assert principal.session is not None
    revoke_session(db, principal.session, "logout")
    audit(db, "auth.logout", principal=principal, resource_type="session", resource_id=principal.session.id)
    clear_session_cookies(response)
    return Message(status="signed_out")


@router.post("/session/refresh", response_model=Message, summary="Renew the current session (rotates the token)")
def refresh_session(
    response: Response,
    principal: Principal = Depends(require_session_user),
    db: DbSession = Depends(get_db, scope="function"),
) -> Message:
    import datetime as dt

    sess = principal.session
    assert sess is not None
    s = get_settings()
    token = new_token(32)
    sess.token_hash = hash_token(token)
    sess.expires_at = utcnow() + dt.timedelta(hours=s.session_ttl_hours)
    sess.last_seen_at = utcnow()
    set_session_cookies(response, token)
    return Message(status="renewed")


@router.get("/me", response_model=MeOut, summary="Current account")
def me(principal: Principal = Depends(require_principal), db: DbSession = Depends(get_db, scope="function")) -> MeOut:
    return build_me(db, principal)


@router.post("/password/forgot", status_code=202, response_model=Message, summary="Request a password reset email")
def forgot_password(body: ForgotIn, request: Request, db: DbSession = Depends(get_db, scope="function")) -> Message:
    hit("forgot-ip", client_ip(request) or "unknown", limit=10, window_seconds=3600)
    hit("forgot-email", body.email, limit=3, window_seconds=3600)
    set_rls(db, system=True)
    auth_service.request_password_reset(db, body.email)
    return Message(status="reset_sent", message="If an account exists for that email, a reset link is on its way.")


@router.post("/password/reset", response_model=Message, summary="Set a new password with a reset token")
def reset_password(
    body: ResetIn, request: Request, response: Response, db: DbSession = Depends(get_db, scope="function")
) -> Message:
    hit("reset-ip", client_ip(request) or "unknown", limit=10, window_seconds=600)
    set_rls(db, system=True)
    auth_service.reset_password(db, body.token, body.password)
    clear_session_cookies(response)
    return Message(status="password_reset", message="Password updated. Sign in with your new password.")


@router.post("/password/change", response_model=Message, summary="Change password (signs out other sessions)")
def change_password(
    body: ChangePasswordIn,
    principal: Principal = Depends(require_session_user),
    db: DbSession = Depends(get_db, scope="function"),
) -> Message:
    hit("change-pw", str(principal.user.id), limit=5, window_seconds=900)
    assert principal.session is not None
    set_rls(db, system=True)
    auth_service.change_password(
        db, principal.user, body.current_password, body.new_password, keep_session=principal.session.id
    )
    return Message(status="password_changed")


@router.post("/reauthenticate", response_model=Message, summary="Confirm password before sensitive actions")
def reauthenticate(
    body: ReauthIn,
    principal: Principal = Depends(require_session_user),
    db: DbSession = Depends(get_db, scope="function"),
) -> Message:
    hit("reauth", str(principal.user.id), limit=10, window_seconds=900)
    if not principal.user.password_hash:
        raise ApiError("Set a password in Settings to confirm sensitive actions.", code="password_not_set")
    if not verify_password(principal.user.password_hash, body.password):
        set_rls(db, system=True)
        audit(db, "auth.reauth_failed", principal=principal, outcome="failure")
        raise ApiError("Incorrect password.", code="invalid_password", status_code=400)
    assert principal.session is not None
    principal.session.reauthenticated_at = utcnow()
    return Message(status="confirmed")


# --- sessions ------------------------------------------------------------------------------------


@router.get("/sessions", response_model=list[SessionOut], summary="List active sessions")
def list_sessions(
    principal: Principal = Depends(require_session_user), db: DbSession = Depends(get_db, scope="function")
) -> list[SessionOut]:
    rows = db.scalars(
        select(Session)
        .where(Session.user_id == principal.user.id, Session.revoked_at.is_(None), Session.expires_at > utcnow())
        .order_by(Session.last_seen_at.desc())
    ).all()
    assert principal.session is not None
    return [
        SessionOut(
            id=r.id,
            created_at=r.created_at,
            last_seen_at=r.last_seen_at,
            expires_at=r.expires_at,
            ip=str(r.ip) if r.ip else None,
            user_agent=r.user_agent,
            auth_method=r.auth_method,
            current=r.id == principal.session.id,
        )
        for r in rows
    ]


@router.delete("/sessions/{session_id}", response_model=Message, summary="Revoke a session")
def delete_session(
    session_id: uuid.UUID,
    principal: Principal = Depends(require_session_user),
    db: DbSession = Depends(get_db, scope="function"),
) -> Message:
    sess = db.scalar(select(Session).where(Session.id == session_id, Session.user_id == principal.user.id))
    if sess is None:
        raise NotFound("Session not found.")
    revoke_session(db, sess, "user_revoked")
    audit(db, "auth.session_revoked", principal=principal, resource_type="session", resource_id=sess.id)
    return Message(status="revoked")


@router.post("/sessions/revoke-others", response_model=Message, summary="Sign out all other sessions")
def revoke_others(
    principal: Principal = Depends(require_session_user), db: DbSession = Depends(get_db, scope="function")
) -> Message:
    assert principal.session is not None
    n = revoke_all_sessions(db, principal.user.id, "user_revoked_all", except_id=principal.session.id)
    audit(db, "auth.sessions_revoked", principal=principal, metadata={"count": n})
    return Message(status="revoked", message=f"{n} session(s) signed out.")


# --- Google ------------------------------------------------------------------------------------


@router.get("/google/start", summary="Begin Google sign-in (redirect)", response_class=RedirectResponse)
def google_start(request: Request, next: str | None = None) -> RedirectResponse:
    hit("oauth-ip", client_ip(request) or "unknown", limit=30, window_seconds=600)
    url, cookie = google_oauth.start(next)
    s = get_settings()
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(
        google_oauth.STATE_COOKIE,
        cookie,
        max_age=google_oauth.STATE_TTL,
        httponly=True,
        secure=s.cookie_secure,
        samesite="lax",
        path="/api/v1/auth/google",
    )
    return resp


@router.get("/google/callback", summary="Google OAuth callback", response_class=RedirectResponse)
def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: DbSession = Depends(get_db, scope="function"),
) -> RedirectResponse:
    s = get_settings()
    web = s.public_web_url.rstrip("/")
    if error or not code or not state:
        return RedirectResponse(f"{web}/login?error=oauth_cancelled", status_code=302)
    try:
        claims = google_oauth.exchange_code(code, state, request.cookies.get(google_oauth.STATE_COOKIE))
        set_rls(db, system=True)
        user = google_oauth.login_or_register(db, claims, client_ip(request))
        if user.status != "active":
            raise ApiError("suspended", code="account_suspended", status_code=403)
    except ApiError as e:
        db.rollback()
        return RedirectResponse(f"{web}/login?error={e.code}", status_code=302)
    except Exception:
        db.rollback()
        return RedirectResponse(f"{web}/login?error=oauth_failed", status_code=302)
    ip = client_ip(request)
    _, token = create_session(db, user, method="google", ip=ip, user_agent=request.headers.get("user-agent"))
    auth_service.after_login(db, user, ip=ip, method="google")
    resp = RedirectResponse(f"{web}{google_oauth.safe_next(str(claims.get('_next')))}", status_code=302)
    set_session_cookies(resp, token)
    resp.delete_cookie(google_oauth.STATE_COOKIE, path="/api/v1/auth/google")
    return resp

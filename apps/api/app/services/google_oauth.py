"""Google sign-in (OAuth 2.0 authorization code + PKCE, OpenID Connect ID token validation).

Disabled unless GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and GOOGLE_REDIRECT_URI are set.
The `state`, `nonce` and PKCE verifier are kept in a short-lived signed (HS256) HttpOnly cookie.
The redirect URI is fixed by configuration — never taken from the request.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
import uuid
from urllib.parse import urlencode

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.core.config import get_settings
from app.core.errors import ApiError
from app.models import OAuthAccount, User, utcnow
from app.security.sessions import revoke_all_sessions
from app.services.audit import audit
from app.services.auth_service import ensure_subscription, find_user_by_email

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - public endpoint URL
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
STATE_COOKIE = "dmx_oauth"
STATE_TTL = 600

_jwks_client: jwt.PyJWKClient | None = None


def safe_next(path: str | None) -> str:
    """Only allow same-site relative paths (prevents open redirects)."""
    if not path or not path.startswith("/") or path.startswith("//") or "\\" in path or "://" in path:
        return "/dashboard"
    return path[:300]


def start(next_path: str | None) -> tuple[str, str]:
    s = get_settings()
    if not s.google_enabled:
        raise ApiError("Google sign-in is not configured.", code="provider_disabled", status_code=404)
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    cookie = jwt.encode(
        {"st": state, "nn": nonce, "pv": verifier, "nx": safe_next(next_path), "exp": int(time.time()) + STATE_TTL},
        s.session_secret.get_secret_value(),
        algorithm="HS256",
    )
    params = {
        "client_id": s.google_client_id,
        "redirect_uri": s.google_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    return f"{AUTH_URL}?{urlencode(params)}", cookie


def _decode_state(cookie: str | None) -> dict[str, str]:
    if not cookie:
        raise ApiError("Sign-in session expired. Please try again.", code="oauth_state_invalid", status_code=400)
    try:
        return jwt.decode(cookie, get_settings().session_secret.get_secret_value(), algorithms=["HS256"])
    except jwt.PyJWTError as e:
        raise ApiError("Sign-in session expired. Please try again.", code="oauth_state_invalid", status_code=400) from e


def _verify_id_token(id_token: str, nonce: str) -> dict[str, object]:
    global _jwks_client
    s = get_settings()
    if _jwks_client is None:
        _jwks_client = jwt.PyJWKClient(JWKS_URL, cache_keys=True, lifespan=3600)
    key = _jwks_client.get_signing_key_from_jwt(id_token)
    claims = jwt.decode(
        id_token,
        key.key,
        algorithms=["RS256"],
        audience=s.google_client_id,
        issuer=ISSUERS,
        options={"require": ["exp", "iat", "iss", "aud", "sub"]},
    )
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise ApiError("Invalid sign-in response.", code="oauth_nonce_mismatch", status_code=400)
    return claims


def exchange_code(code: str, state: str, cookie: str | None) -> dict[str, object]:
    s = get_settings()
    if not s.google_enabled:
        raise ApiError("Google sign-in is not configured.", code="provider_disabled", status_code=404)
    st = _decode_state(cookie)
    if not secrets.compare_digest(st["st"], state or ""):
        raise ApiError("Invalid sign-in state.", code="oauth_state_invalid", status_code=400)
    assert s.google_client_secret is not None
    resp = httpx.post(
        TOKEN_URL,
        data={
            "code": code,
            "client_id": s.google_client_id,
            "client_secret": s.google_client_secret.get_secret_value(),
            "redirect_uri": s.google_redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": st["pv"],
        },
        timeout=10,
    )
    if resp.status_code != 200:
        raise ApiError("Google sign-in failed. Please try again.", code="oauth_exchange_failed", status_code=400)
    id_token = resp.json().get("id_token")
    if not id_token:
        raise ApiError("Google sign-in failed. Please try again.", code="oauth_exchange_failed", status_code=400)
    claims = _verify_id_token(id_token, st["nn"])
    claims["_next"] = st["nx"]
    return claims


def login_or_register(db: DbSession, claims: dict[str, object], ip: str | None) -> User:
    sub = str(claims["sub"])
    email = str(claims.get("email") or "").lower()
    if not email or claims.get("email_verified") is not True:
        raise ApiError("Your Google account email is not verified.", code="oauth_email_unverified", status_code=400)
    link = db.scalar(
        select(OAuthAccount).where(OAuthAccount.provider == "google", OAuthAccount.provider_subject == sub)
    )
    if link is not None:
        user = db.get(User, link.user_id)
        if user is None or user.deleted_at is not None:
            raise ApiError("This account is no longer available.", code="account_unavailable", status_code=403)
        return user
    user = find_user_by_email(db, email)
    if user is None:
        user = User(
            id=uuid.uuid4(),
            email=email,
            password_hash=None,
            display_name=str(claims.get("name") or "")[:120] or None,
            email_verified_at=utcnow(),
            status="active",
        )
        db.add(user)
        db.flush()
        ensure_subscription(db, user)
        audit(
            db,
            "auth.register",
            actor_type="user",
            actor_user_id=user.id,
            tenant_user_id=user.id,
            resource_type="user",
            resource_id=user.id,
            ip=ip,
            metadata={"method": "google"},
        )
    elif user.email_verified_at is None:
        # Account pre-hijacking defence: an unverified password account with this email may have
        # been created by someone else. Google proved mailbox ownership, so drop that password
        # and any sessions before linking.
        user.password_hash = None
        user.email_verified_at = utcnow()
        revoke_all_sessions(db, user.id, "oauth_takeover_protection")
    db.add(OAuthAccount(id=uuid.uuid4(), user_id=user.id, provider="google", provider_subject=sub, email=email))
    audit(
        db,
        "auth.oauth_linked",
        actor_type="user",
        actor_user_id=user.id,
        tenant_user_id=user.id,
        resource_type="user",
        resource_id=user.id,
        ip=ip,
        metadata={"provider": "google"},
    )
    return user

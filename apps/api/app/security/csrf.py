"""CSRF protection for cookie-authenticated, state-changing requests.

Signed double-submit cookie: the `dmx_csrf` cookie holds `<random>.<hmac(secret, random)>`; the
client must echo it in the `X-CSRF-Token` header. Additionally, when an `Origin` header is
present it must be one of the configured web origins. Requests authenticated with an
`Authorization: Bearer` API key are exempt (browsers can't attach that header cross-site
without a CORS preflight, which we don't allow for foreign origins).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import get_settings
from app.core.errors import error_body

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
EXEMPT_PATHS = {"/api/v1/billing/webhooks/paddle"}


def _sign(value: str) -> str:
    key = get_settings().session_secret.get_secret_value().encode()
    return hmac.new(key, f"csrf:{value}".encode(), hashlib.sha256).hexdigest()[:32]


def new_csrf_token() -> str:
    r = secrets.token_urlsafe(24)
    return f"{r}.{_sign(r)}"


def valid_csrf_token(token: str | None) -> bool:
    if not token or "." not in token or len(token) > 128:
        return False
    r, sig = token.rsplit(".", 1)
    return hmac.compare_digest(sig, _sign(r))


def _origin_allowed(origin: str) -> bool:
    s = get_settings()
    allowed = {o.rstrip("/") for o in [*s.cors_origins, s.public_web_url]}
    parts = urlsplit(origin)
    return f"{parts.scheme}://{parts.netloc}" in allowed


class CSRFMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in SAFE_METHODS:
            await self.app(scope, receive, send)
            return
        path: str = scope["path"]
        if not path.startswith("/api/") or path in EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            await self.app(scope, receive, send)
            return
        origin = request.headers.get("origin")
        if origin and not _origin_allowed(origin):
            await JSONResponse(error_body("csrf_failed", "Cross-origin request rejected."), status_code=403)(
                scope, receive, send
            )
            return
        cookie = request.cookies.get(get_settings().csrf_cookie_name)
        header = request.headers.get("x-csrf-token")
        if not (cookie and header and hmac.compare_digest(cookie, header) and valid_csrf_token(cookie)):
            await JSONResponse(
                error_body("csrf_failed", "Missing or invalid CSRF token. Reload the page and try again."),
                status_code=403,
            )(scope, receive, send)
            return
        await self.app(scope, receive, send)

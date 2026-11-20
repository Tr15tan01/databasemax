"""Request id, security headers, body size limit, access log, API request counting."""

from __future__ import annotations

import logging
import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings
from app.core.errors import error_body
from app.core.logging import log_event, request_id_var

log = logging.getLogger("dmx.access")
_RID_RE = re.compile(r"^[A-Za-z0-9_.-]{8,64}$")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get("x-request-id", "")
        rid = incoming if _RID_RE.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(rid)
        start = time.perf_counter()
        request.state.request_id = rid
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        response.headers["X-Request-ID"] = rid
        # Security headers for API responses (Caddy adds page-level CSP for the web app).
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        if get_settings().cookie_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
        # Never log query strings (may contain one-time tokens) or bodies.
        request_id_var.set(rid)
        log_event(
            log,
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
            user_id=getattr(request.state, "user_id", None),
        )
        request_id_var.set(None)
        return response


class BodySizeLimitMiddleware:
    """Reject bodies over the configured limit, including chunked uploads without Content-Length."""

    def __init__(self, app: ASGIApp, max_bytes: int | None = None):
        self.app = app
        self.max_bytes = max_bytes or get_settings().max_request_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        cl = headers.get(b"content-length")
        if cl is not None and cl.isdigit() and int(cl) > self.max_bytes:
            await JSONResponse(error_body("payload_too_large", "Request body too large."), status_code=413)(
                scope, receive, send
            )
            return
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _TooLarge()
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _TooLarge:
            await JSONResponse(error_body("payload_too_large", "Request body too large."), status_code=413)(
                scope, receive, send
            )


class _TooLarge(Exception):
    pass

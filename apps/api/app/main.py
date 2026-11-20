"""Public API application: `uvicorn app.main:app`."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import BodySizeLimitMiddleware, RequestContextMiddleware
from app.schemas.common import ErrorResponse
from app.security.csrf import CSRFMiddleware

API_DESCRIPTION = """
The DatabaseMax HTTP API. Everything you can do in the dashboard is available here.

**Authentication.** Send an API key as `Authorization: Bearer dmx_live_…`. Keys are created in the
dashboard (Settings → API keys) and carry explicit scopes. Browser sessions use an HttpOnly cookie
plus an `X-CSRF-Token` header on state-changing requests.

**Errors** use one shape: `{"error": {"code", "message", "request_id", "details?"}}`.
Every response carries an `X-Request-ID` header.

**Idempotency.** `POST /projects` and `POST /databases` accept an `Idempotency-Key` header; retrying
with the same key returns the original result instead of creating a duplicate.

**Asynchronous provisioning.** `POST /databases` returns `202` immediately; poll
`GET /databases/{id}` (or `/status`) until `status` is `ready`.
"""


def create_app() -> FastAPI:
    s = get_settings()
    configure_logging(s.log_level)
    app = FastAPI(
        title="DatabaseMax API",
        version="1.0.0",
        description=API_DESCRIPTION,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        responses={
            401: {"model": ErrorResponse, "description": "Not authenticated"},
            403: {"model": ErrorResponse, "description": "Forbidden"},
            404: {"model": ErrorResponse, "description": "Not found"},
            422: {"model": ErrorResponse, "description": "Validation error"},
            429: {"model": ErrorResponse, "description": "Rate limited"},
        },
    )
    install_error_handlers(app)

    from fastapi import Request
    from fastapi.responses import JSONResponse

    from app.services.approvals import ApprovalRequired

    @app.exception_handler(ApprovalRequired)
    async def _approval_required(_: Request, exc: ApprovalRequired) -> JSONResponse:
        return JSONResponse(
            {"status": "pending_approval", "approval": exc.body},
            status_code=202,
            headers={"X-DMX-Approval": "pending"},
        )

    from app.api.v1 import (
        account,
        activity,
        admin,
        api_keys,
        approvals,
        auth,
        backups,
        billing,
        console,
        databases,
        dev,
        meta,
        notifications,
        projects,
        usage,
    )

    for r in (
        meta,
        auth,
        account,
        projects,
        databases,
        console,
        backups,
        usage,
        billing,
        api_keys,
        approvals,
        activity,
        notifications,
        admin,
    ):
        app.include_router(r.router, prefix="/api/v1")
    if not s.is_production:
        app.include_router(dev.router, prefix="/api/v1")

    # Order: outermost last. Request context wraps everything so errors carry request ids.
    app.add_middleware(CSRFMiddleware)
    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "Idempotency-Key", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Retry-After", "Location", "Idempotent-Replayed"],
        max_age=600,
    )
    app.add_middleware(RequestContextMiddleware)
    return app


app = create_app()

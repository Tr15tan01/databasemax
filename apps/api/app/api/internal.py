"""Internal API — served by a separate ASGI app on a private port (never routed by Caddy).

Consumers: the PostgreSQL gateway (route lookup). Authenticated with INTERNAL_API_TOKEN.
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import system_session
from app.models import Database, InfrastructureHost, User

router = APIRouter(prefix="/internal/v1")


def internal_auth(authorization: str = Header(default="")) -> None:
    expected = f"Bearer {get_settings().internal_api_token.get_secret_value()}"
    if not hmac.compare_digest(authorization.encode(), expected.encode()):
        raise HTTPException(401, "unauthorized")


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/gateway/route", dependencies=[Depends(internal_auth)])
def gateway_route(
    username: str = Query(..., max_length=63), route_id: str | None = Query(None, max_length=32)
) -> dict[str, object]:
    if not username.startswith("u_") or len(username) < 6:
        raise HTTPException(404, "not found")
    rid = "db-" + username[2:]
    if route_id is not None and route_id != rid:
        raise HTTPException(404, "not found")
    with system_session() as db:
        row = db.execute(
            select(Database, User.status)
            .join(User, User.id == Database.owner_id)
            .where(Database.route_id == rid, Database.status != "deleted")
        ).first()
        if row is None:
            raise HTTPException(404, "not found")
        d, user_status = row
        if not d.internal_endpoint_host:
            raise HTTPException(404, "not found")
        status = d.status
        if d.suspended_by_admin or user_status != "active":
            status = "suspended"
        elif d.host_id is not None:
            host_status = db.scalar(select(InfrastructureHost.status).where(InfrastructureHost.id == d.host_id))
            if host_status == "offline":
                status = "host_unavailable"
        return {
            "route_id": d.route_id,
            "username": username,
            "backend_host": d.internal_endpoint_host,
            "backend_port": d.internal_endpoint_port or 5432,
            "max_connections": d.max_connections,
            "status": status,
        }

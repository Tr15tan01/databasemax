"""Public metadata: health, plans, service status, public configuration (no secrets)."""

from __future__ import annotations

import datetime as dt

import httpx
import redis
from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db, set_rls
from app.core.redis import get_redis
from app.models import Plan, ProvisioningJob, WorkerHeartbeat, utcnow
from app.provisioning.client import ProvisionerClient, ProvisionerError

router = APIRouter(tags=["meta"])


@router.get("/health", summary="Liveness/readiness of the API")
def health(db: Session = Depends(get_db, scope="function")) -> dict[str, str]:
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.get("/plans", summary="Available plans and their limits")
def list_plans(db: Session = Depends(get_db, scope="function")) -> list[dict[str, object]]:
    rows = db.scalars(select(Plan).where(Plan.active.is_(True)).order_by(Plan.sort_order)).all()
    return [
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "price_cents": p.price_cents,
            "currency": p.currency,
            "features": p.features,
            "limits": p.entitlements,
        }
        for p in rows
    ]


@router.get("/config/public", summary="Public client configuration")
def public_config() -> dict[str, object]:
    s = get_settings()
    return {
        "google_auth": s.google_enabled,
        "billing": {
            "provider": "paddle",
            "enabled": s.paddle_enabled,
            "environment": s.paddle_environment,
            "client_token": s.paddle_client_token if s.paddle_enabled else None,
        },
        "regions": [{"id": s.region, "label": s.region_label}],
        "postgres_versions": s.supported_pg_versions,
        "db_public_port": s.db_public_port,
    }


def _component(name: str, ok: bool | None, detail: str) -> dict[str, object]:
    return {
        "name": name,
        "status": "operational" if ok else ("unknown" if ok is None else "degraded"),
        "detail": detail,
    }


@router.get("/status", summary="Live service status (real checks, no history fabricated)")
def service_status(db: Session = Depends(get_db, scope="function")) -> dict[str, object]:
    set_rls(db, system=True)
    comps = []
    try:
        db.execute(text("SELECT 1"))
        comps.append(_component("API", True, "Responding"))
    except Exception:
        comps.append(_component("API", False, "Control database unreachable"))
    try:
        get_redis().ping()
        redis_ok = True
    except redis.RedisError:
        redis_ok = False
    try:
        ProvisionerClient(timeout=3).health()
        prov_ok = True
    except ProvisionerError:
        prov_ok = False
    hb = db.scalar(select(func.max(WorkerHeartbeat.last_seen_at)))
    worker_ok = bool(hb and utcnow() - hb < dt.timedelta(minutes=2))
    queued = db.scalar(select(func.count(ProvisioningJob.id)).where(ProvisioningJob.status.in_(("queued", "running"))))
    comps.append(
        _component(
            "Database provisioning",
            prov_ok and worker_ok and redis_ok,
            f"{queued or 0} operation(s) in progress" if prov_ok and worker_ok else "Provisioning is delayed",
        )
    )
    s = get_settings()
    gw_ok: bool | None = None
    gw_url = s.gateway_status_url
    if gw_url:
        try:
            gw_ok = httpx.get(gw_url, timeout=2).status_code == 200
        except httpx.HTTPError:
            gw_ok = False
    comps.append(
        _component(
            "PostgreSQL gateway",
            gw_ok,
            "Accepting TLS connections" if gw_ok else ("Not monitored" if gw_ok is None else "Not responding"),
        )
    )
    overall = "operational" if all(c["status"] in ("operational", "unknown") for c in comps) else "degraded"
    return {"status": overall, "components": comps, "region": s.region_label, "checked_at": utcnow().isoformat()}

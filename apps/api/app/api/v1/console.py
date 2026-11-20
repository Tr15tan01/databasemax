"""Dashboard tools for one database: SQL editor, schema browser, health insights and the live
load monitor.

The SQL editor, schema browser and insights connect to the customer's database through the
gateway as the customer's own role (see app/sqlconsole/engine.py) and are available to the
signed-in owner only — not to API keys, whose destructive actions go through approvals. Running
SQL also requires a recent password confirmation, like revealing the connection password.
SQL text and results are never logged or stored; the audit log records that a query ran, its
statement types and size of the result.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
import time
import uuid
from typing import Any

import httpx
import redis
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db, system_session
from app.core.errors import ApiError, ServiceUnavailable
from app.core.ratelimit import hit
from app.core.redis import get_redis
from app.core.slots import slot
from app.models import Database, UsageRecord, utcnow
from app.provisioning.client import ProvisionerError
from app.provisioning.connectivity import gateway_params
from app.provisioning.hosts import HostUnavailable, ProvisionerPool
from app.schemas.console import InsightsOut, LiveOut, SchemaOut, SqlRun, SqlRunOut
from app.security.deps import Principal, require_recent_auth, require_scope, require_session_user, require_verified
from app.security.secrets_store import secret_store
from app.services import database_service as svc
from app.services import plans
from app.services.audit import audit
from app.sqlconsole import catalog, engine, lexer

router = APIRouter(prefix="/databases", tags=["database tools"])

EDITOR_LIMITS = engine.Limits()
EXPLAIN_LIMITS = engine.Limits(max_cell_chars=None, max_rows=10, max_bytes=2 * 1024 * 1024)
CATALOG_LIMITS = engine.Limits(timeout_seconds=10.0, max_cell_chars=None, max_rows=5000, max_bytes=4 * 1024 * 1024)
# Concurrent editor connections: one per user, and a ceiling for the whole API (bounded memory).
GLOBAL_EDITOR_SLOTS = 10


def _ready_database(db: Session, principal: Principal, database_id: uuid.UUID) -> Database:
    d = svc.get_database_for(db, principal, database_id)
    if d.status != "ready" or d.suspended_by_admin:
        raise ApiError("The database must be running to use this tool.", code="database_not_ready", status_code=409)
    return d


def _params(db: Session, d: Database) -> dict[str, Any]:
    cred = svc.active_credential(db, d)
    if cred is None or cred.secret_reference is None:
        raise ApiError("The database has no active credential yet.", code="database_not_ready", status_code=409)
    password = secret_store.get(db, cred.secret_reference)
    return gateway_params(d, cred.username, password)


def _execute(
    db: Session, principal: Principal, params: dict[str, Any], sql: str, *, read_only: bool, limits: engine.Limits
) -> engine.Outcome:
    # Release the control-plane connection while the customer's query runs (the RLS context is
    # re-applied on the next transaction).
    db.commit()
    uid = str(principal.user.id)
    ttl = limits.timeout_seconds + 30
    busy = "Another query is still running. Wait for it to finish, then try again."
    with (
        slot(f"sql:user:{uid}", 1, ttl, busy_message=busy, busy_code="query_running"),
        slot("sql:global", GLOBAL_EDITOR_SLOTS, ttl, busy_message="The SQL editor is busy. Try again in a moment."),
    ):
        try:
            return engine.run(params, sql, read_only=read_only, limits=limits)
        except engine.ConnectFailed as e:
            raise ApiError(str(e), code="database_unreachable", status_code=502) from e


# --- SQL editor ---------------------------------------------------------------------------------


@router.post("/{database_id}/sql", response_model=SqlRunOut, summary="Run SQL in the dashboard's SQL editor")
def run_sql(
    database_id: uuid.UUID,
    body: SqlRun,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_session_user),
) -> SqlRunOut:
    require_verified(principal)
    require_recent_auth(principal)
    d = _ready_database(db, principal, database_id)
    statements = lexer.split(body.sql)
    if not statements:
        raise ApiError("Enter a SQL statement to run.", code="empty_query", status_code=422)
    sql, read_only, limits = body.sql, body.mode == "read", EDITOR_LIMITS
    if body.explain != "none":
        stmt = lexer.single_statement(body.sql)
        if stmt is None:
            raise ApiError(
                "Explain works on exactly one statement. Select or keep just the query you want to analyze.",
                code="explain_single_statement",
                status_code=422,
            )
        if statements[0].keyword == "EXPLAIN":
            raise ApiError(
                "Remove EXPLAIN from the query; the Explain button adds it.", code="explain_nested", status_code=422
            )
        options = "FORMAT JSON, ANALYZE, BUFFERS" if body.explain == "analyze" else "FORMAT JSON"
        # Always read-only and rolled back: ANALYZE executes the statement.
        sql, read_only, limits = f"EXPLAIN ({options}) {stmt}", True, EXPLAIN_LIMITS
    elif not read_only and not body.confirm_destructive:
        kinds = lexer.destructive_kinds(statements)
        if kinds:
            raise ApiError(
                "These statements change or remove existing data. Confirm to run them.",
                code="confirmation_required",
                status_code=409,
                details={"kinds": kinds},
            )
    hit("sql-editor", str(principal.user.id), 30, 60)
    params = _params(db, d)
    out = _execute(db, principal, params, sql, read_only=read_only, limits=limits)
    plan: Any = None
    results = out.results
    if body.explain != "none" and out.error is None:
        try:
            plan = json.loads(out.results[0].rows[0][0] or "null")
        except (IndexError, ValueError, TypeError):
            out.error = engine.SqlError("The query plan couldn't be read (it may be too large).")
        results = []
    keywords = list(dict.fromkeys(s.keyword for s in statements))[:10]
    audit(
        db,
        "database.sql_executed",
        principal=principal,
        resource_type="database",
        resource_id=d.id,
        outcome="failure" if out.error else "success",
        metadata={
            "mode": "explain" if body.explain != "none" else body.mode,
            "statements": len(statements),
            "statement_types": keywords,
            "rows": sum(r.row_count for r in out.results),
            "duration_ms": out.duration_ms,
            "timed_out": out.timed_out,
            "truncated": out.truncated,
            "sqlstate": out.error.sqlstate if out.error else None,
        },
    )
    return SqlRunOut.model_validate(
        {
            "mode": "read" if read_only else "write",
            "statement_count": len(statements),
            "results": [
                {
                    "kind": r.kind,
                    "command": r.command,
                    "columns": [{"name": c.name, "type": c.type} for c in r.columns],
                    "rows": r.rows,
                    "row_count": r.row_count,
                    "truncated": r.truncated,
                }
                for r in results
            ],
            "error": vars(out.error) if out.error else None,
            "notices": out.notices,
            "duration_ms": out.duration_ms,
            "timed_out": out.timed_out,
            "truncated": out.truncated,
            "rolled_back_open_transaction": out.rolled_back_open_transaction,
            "omitted_result_sets": out.omitted_result_sets,
            "plan": plan,
            "limits": {
                "timeout_seconds": limits.timeout_seconds,
                "max_rows": limits.max_rows,
                "max_bytes": limits.max_bytes,
            },
        }
    )


# --- schema browser -----------------------------------------------------------------------------


@router.get("/{database_id}/schema", response_model=SchemaOut, summary="Tables, views and columns")
def get_schema(
    database_id: uuid.UUID,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_session_user),
) -> SchemaOut:
    d = _ready_database(db, principal, database_id)
    hit("db-schema", str(principal.user.id), 30, 60)
    out = _execute(db, principal, _params(db, d), catalog.SCHEMA_SQL, read_only=True, limits=CATALOG_LIMITS)
    if out.error:
        raise ApiError(f"Couldn't read the schema: {out.error.message}", code="schema_failed", status_code=502)
    return SchemaOut.model_validate(catalog.parse_schema(out))


# --- insights -----------------------------------------------------------------------------------


def _series(db: Session, d: Database, metric: str, since: dt.datetime) -> list[tuple[dt.datetime, float]]:
    rows = db.execute(
        select(UsageRecord.measured_at, UsageRecord.quantity)
        .where(UsageRecord.database_id == d.id, UsageRecord.metric == metric, UsageRecord.measured_at >= since)
        .order_by(UsageRecord.measured_at)
    ).all()
    return [(r[0], float(r[1])) for r in rows]


def storage_forecast(points: list[tuple[dt.datetime, float]], used: int | None, limit_bytes: int) -> dict[str, Any]:
    """Least-squares growth rate over the samples, projected to the allocation."""
    out: dict[str, Any] = {
        "used_bytes": used,
        "limit_bytes": limit_bytes,
        "growth_bytes_per_day": None,
        "days_until_full": None,
        "based_on_days": None,
    }
    if len(points) < 6:
        return out
    t0 = points[0][0]
    xs = [(t - t0).total_seconds() / 86400 for t, _ in points]
    ys = [v for _, v in points]
    span = xs[-1] - xs[0]
    if span < 0.25:  # less than six hours of history: too noisy to project
        return out
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    var = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / var if var else 0.0
    out["growth_bytes_per_day"] = round(slope, 1)
    out["based_on_days"] = round(span, 1)
    current = used if used is not None else ys[-1]
    if slope > 0 and current < limit_bytes:
        out["days_until_full"] = round((limit_bytes - current) / slope, 1)
    elif current >= limit_bytes:
        out["days_until_full"] = 0.0
    return out


def _capacity_findings(db: Session, d: Database, forecast: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    since = utcnow() - dt.timedelta(hours=24)
    profile = plans.PROFILES.get(d.compute_profile, {})
    days = forecast["days_until_full"]
    used, limit_b = forecast["used_bytes"], forecast["limit_bytes"]
    if used is not None and limit_b and used / limit_b >= 0.85:
        findings.append(
            {
                "id": "storage_used",
                "severity": "critical" if used / limit_b >= 0.95 else "warning",
                "category": "capacity",
                "title": f"Storage is {used / limit_b:.0%} full",
                "detail": "At 100% the database switches to read-only until space is freed or the allocation grows.",
                "suggestion": "Increase the storage allocation in Settings, or delete data you no longer need.",
            }
        )
    elif days is not None and days < 30:
        findings.append(
            {
                "id": "storage_forecast",
                "severity": "critical" if days < 7 else "warning",
                "category": "capacity",
                "title": f"At the current growth rate, storage fills up in about {max(days, 0):.0f} days",
                "detail": f"Storage grows by about {forecast['growth_bytes_per_day'] / 1024**2:.1f} MB per day "
                f"(measured over the last {forecast['based_on_days']:.0f} days).",
                "suggestion": "Increase the storage allocation in Settings before it runs out.",
            }
        )
    conns = [v for _, v in _series(db, d, "connections", since)]
    if conns and d.max_connections and max(conns) / d.max_connections >= 0.8:
        peak = max(conns)
        findings.append(
            {
                "id": "connections",
                "severity": "critical" if peak >= d.max_connections else "warning",
                "category": "capacity",
                "title": f"Connections peaked at {int(peak)} of {d.max_connections} in the last 24 hours",
                "detail": "New connections are refused once the limit is reached.",
                "suggestion": "Use a connection pool in your application (5 to 10 connections per process).",
            }
        )
    cpus = float(profile.get("cpus") or 0)
    cpu = [v for _, v in _series(db, d, "cpu_percent", since)]
    if cpu and cpus:
        avg = sum(cpu) / len(cpu) / cpus
        if avg >= 80:
            findings.append(
                {
                    "id": "cpu",
                    "severity": "warning",
                    "category": "capacity",
                    "title": f"CPU averaged {avg:.0f}% of the limit over 24 hours",
                    "detail": "Queries slow down when the database is CPU-bound.",
                    "suggestion": "Optimize the heaviest queries (Explain in the SQL editor) or choose a larger size.",
                }
            )
    mem_mb = float(profile.get("memory_mb") or 0)
    mem = [v for _, v in _series(db, d, "memory_bytes", since)]
    if mem and mem_mb and max(mem) / (mem_mb * 1024 * 1024) >= 0.9:
        findings.append(
            {
                "id": "memory",
                "severity": "warning",
                "category": "capacity",
                "title": f"Memory peaked at {max(mem) / (mem_mb * 1024 * 1024):.0%} of the limit",
                "detail": "Near the limit, PostgreSQL may be stopped by the out-of-memory killer.",
                "suggestion": "Lower the number of connections or choose a larger size.",
            }
        )
    return findings


@router.get("/{database_id}/insights", response_model=InsightsOut, summary="Health check and recommendations")
def get_insights(
    database_id: uuid.UUID,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_session_user),
) -> InsightsOut:
    d = _ready_database(db, principal, database_id)
    hit("db-insights", str(principal.user.id), 10, 60)
    limit_bytes = d.allocated_storage_mb * 1024 * 1024
    forecast = storage_forecast(
        _series(db, d, "storage_bytes", utcnow() - dt.timedelta(days=14)), d.storage_used_bytes, limit_bytes
    )
    capacity = _capacity_findings(db, d, forecast)
    params = _params(db, d)
    out = _execute(db, principal, params, catalog.INSIGHTS_SQL, read_only=True, limits=CATALOG_LIMITS)
    if out.error:
        raise ApiError(f"Couldn't analyze the database: {out.error.message}", code="insights_failed", status_code=502)
    findings, facts = catalog.analyze(out)
    findings = capacity + findings
    order = {"critical": 0, "warning": 1, "info": 2, "ok": 3}
    findings.sort(key=lambda f: order[f["severity"]])
    score = max(0, 100 - sum(catalog.WEIGHTS[f["severity"]] for f in findings))
    if not findings:
        findings.append(
            {
                "id": "all_clear",
                "severity": "ok",
                "category": "performance",
                "title": "No problems found",
                "detail": "Indexes, vacuum, connections, storage and sequences all look healthy.",
            }
        )
    return InsightsOut.model_validate(
        {
            "score": score,
            "grade": catalog.grade(score),
            "generated_at": utcnow(),
            "stats_since": facts["stats_since"],
            "cache_hit_ratio": facts["cache_hit_ratio"],
            "findings": findings,
            "forecast": forecast,
        }
    )


# --- live monitor -------------------------------------------------------------------------------

_gw_cache: dict[str, Any] = {"at": 0.0, "data": None}
_gw_lock = threading.Lock()


def _gateway_traffic(route_id: str) -> tuple[int | None, int | None, int | None]:
    """(bytes in, bytes out, active connections) for one route from the gateway's status endpoint,
    fetched at most every 1.5 s per API process."""
    url = get_settings().gateway_status_url
    if not url:
        return None, None, None
    with _gw_lock:
        if time.monotonic() - _gw_cache["at"] > 1.5:
            try:
                _gw_cache["data"] = httpx.get(url, timeout=2).json()
            except (httpx.HTTPError, ValueError):
                _gw_cache["data"] = None
            _gw_cache["at"] = time.monotonic()
        data = _gw_cache["data"]
    if not data:
        return None, None, None
    t = (data.get("traffic_by_route") or {}).get(route_id) or {}
    active = (data.get("active_by_route") or {}).get(route_id, 0)
    return t.get("bytes_in", 0), t.get("bytes_out", 0), int(active)


def _rate(cur: dict[str, Any], prev: dict[str, Any] | None, key: str, interval: float | None) -> float | None:
    if not prev or not interval or cur.get(key) is None or prev.get(key) is None:
        return None
    delta = cur[key] - prev[key]
    return round(delta / interval, 2) if delta >= 0 else None  # counter reset: skip


@router.get(
    "/{database_id}/live", response_model=LiveOut, summary="Current load (for a live view; poll every few seconds)"
)
def get_live(
    database_id: uuid.UUID,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_scope("usage:read")),
) -> LiveOut:
    d = svc.get_database_for(db, principal, database_id)
    hit("db-live", str(principal.user.id), 120, 60)
    empty: dict[str, Any] = {
        k: None for k in LiveOut.model_fields if k not in ("measured_at", "state", "max_connections")
    }
    if d.status != "ready":
        return LiveOut(measured_at=utcnow(), state=d.status, max_connections=d.max_connections, **empty)
    r = get_redis()
    resp_key, prev_key = f"dmx:live:resp:{d.id}", f"dmx:live:prev:{d.id}"
    try:
        cached = r.get(resp_key)
    except redis.RedisError:
        cached = None
    if cached:
        return LiveOut.model_validate_json(cached)
    cred = svc.active_credential(db, d)
    if cred is None:
        return LiveOut(measured_at=utcnow(), state="starting", max_connections=d.max_connections, **empty)
    try:
        with system_session() as sdb:
            client = ProvisionerPool(timeout=10).for_host_id(sdb, d.host_id)
        sample = client.live(d.internal_database_name, d.pg_database_name, cred.username)
    except (ProvisionerError, HostUnavailable) as e:
        raise ServiceUnavailable("Live metrics are temporarily unavailable.") from e
    m = sample.get("measured") or {}
    net_in, net_out, gw_active = _gateway_traffic(d.route_id)
    now = time.time()
    cur = {
        "ts": now,
        "xact": (m.get("xact_commit") or 0) + (m.get("xact_rollback") or 0) if "xact_commit" in m else None,
        "rollback": m.get("xact_rollback"),
        "rows_read": m.get("rows_read"),
        "rows_written": m.get("rows_written"),
        "blks_hit": m.get("blks_hit"),
        "blks_read": m.get("blks_read"),
        "net_in": net_in,
        "net_out": net_out,
    }
    try:
        raw_prev = r.get(prev_key)
        prev = json.loads(raw_prev) if raw_prev else None
    except (redis.RedisError, ValueError):
        prev = None
    interval = now - prev["ts"] if prev else None
    if interval is not None and not 0.5 <= interval <= 120:
        prev, interval = None, None
    hit_delta = _rate(cur, prev, "blks_hit", 1.0)
    read_delta = _rate(cur, prev, "blks_read", 1.0)
    cache_ratio = None
    if hit_delta is not None and read_delta is not None and hit_delta + read_delta > 0:
        cache_ratio = round(hit_delta / (hit_delta + read_delta), 4)
    cpus = float(plans.PROFILES.get(d.compute_profile, {}).get("cpus") or 0)
    cpu = m.get("cpu_percent")
    out = LiveOut(
        measured_at=utcnow(),
        state=sample.get("state", "unknown"),
        interval_seconds=round(interval, 2) if interval else None,
        cpu_percent_of_limit=round(min(cpu / cpus, 100.0), 1) if cpu is not None and cpus else None,
        memory_bytes=m.get("memory_usage_bytes"),
        memory_limit_bytes=m.get("memory_limit_bytes"),
        sessions_active=m.get("sessions_active"),
        sessions_idle=m.get("sessions_idle"),
        sessions_idle_in_transaction=m.get("sessions_idle_in_transaction"),
        sessions_waiting_on_locks=m.get("sessions_waiting_on_locks"),
        max_connections=d.max_connections,
        gateway_connections=gw_active,
        longest_query_seconds=m.get("longest_query_seconds"),
        oldest_transaction_seconds=m.get("oldest_transaction_seconds"),
        transactions_per_second=_rate(cur, prev, "xact", interval),
        rollbacks_per_second=_rate(cur, prev, "rollback", interval),
        rows_read_per_second=_rate(cur, prev, "rows_read", interval),
        rows_written_per_second=_rate(cur, prev, "rows_written", interval),
        network_in_bytes_per_second=_rate(cur, prev, "net_in", interval),
        network_out_bytes_per_second=_rate(cur, prev, "net_out", interval),
        cache_hit_ratio=cache_ratio,
        deadlocks_total=m.get("deadlocks"),
        temp_bytes_total=m.get("temp_bytes"),
    )
    try:
        pipe = r.pipeline()
        if prev is None or (interval or 0) >= 2:
            pipe.set(prev_key, json.dumps(cur), ex=180)
        # Viewers of the same database share one sample for 2 s (one provisioner call per 2 s at most).
        pipe.set(resp_key, out.model_dump_json(), ex=2)
        pipe.execute()
    except redis.RedisError:
        pass
    return out

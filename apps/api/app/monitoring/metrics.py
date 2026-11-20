"""Usage measurement, storage-limit enforcement, threshold alerts, rollups and retention.

Measured metrics (source='measured') come from PostgreSQL itself (pg_database_size,
pg_stat_activity) and from each instance's cgroup (memory, CPU). Nothing is estimated here; when a
value can't be measured it's simply not recorded.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.core.logging import log_event
from app.models import (
    ApiRequestCounter,
    Database,
    DatabaseCredential,
    InfrastructureHost,
    Project,
    UsageRecord,
    User,
    utcnow,
)
from app.provisioning import state_machine as sm
from app.provisioning.client import ProvisionerClient, ProvisionerError
from app.provisioning.hosts import HostUnavailable, ProvisionerPool, bootstrap_host
from app.services import plans
from app.services.audit import audit
from app.services.notifications import notify

log = logging.getLogger("dmx.metrics")

RAW_RETENTION = dt.timedelta(days=7)
HOURLY_RETENTION = dt.timedelta(days=90)
DAILY_RETENTION = dt.timedelta(days=400)

DB_METRICS = {
    "storage_bytes": "database_size_bytes",
    "connections": "active_connections",
    "memory_bytes": "memory_usage_bytes",
    "cpu_percent": "cpu_percent",
    "uptime_seconds": "uptime_seconds",
    "transactions_total": "transactions_total",
    "rows_written_total": "rows_written_total",
    "rows_read_total": "rows_read_total",
    "volume_used_bytes": "fs_used_bytes",  # hard-limited (loop) volumes only
}


def _period(ts: dt.datetime) -> str:
    return ts.strftime("%Y-%m")


def record(
    db: Session,
    *,
    user_id: uuid.UUID,
    metric: str,
    quantity: float,
    project_id: uuid.UUID | None = None,
    database_id: uuid.UUID | None = None,
    source: str = "measured",
    at: dt.datetime | None = None,
) -> None:
    at = at or utcnow()
    db.add(
        UsageRecord(
            id=uuid.uuid4(),
            user_id=user_id,
            project_id=project_id,
            database_id=database_id,
            metric=metric,
            quantity=float(quantity),
            source=source,
            granularity="raw",
            measured_at=at,
            period=_period(at),
        )
    )


def _credential(db: Session, d: Database) -> DatabaseCredential | None:
    return db.scalar(
        select(DatabaseCredential).where(
            DatabaseCredential.database_id == d.id,
            DatabaseCredential.activated_at.is_not(None),
            DatabaseCredential.revoked_at.is_(None),
        )
    )


def _fetch(prov: ProvisionerClient, iid: str, database: str, username: str) -> dict[str, Any] | ProvisionerError:
    try:
        return prov.stats(iid, database, username)
    except ProvisionerError as e:
        return e


def collect_database(db: Session, prov: ProvisionerClient, d: Database) -> dict[str, Any] | None:
    cred = _credential(db, d)
    if cred is None:
        return None
    return _apply(db, prov, d, cred.username, _fetch(prov, d.internal_database_name, d.pg_database_name, cred.username))


def _apply(
    db: Session, prov: ProvisionerClient, d: Database, username: str, out: dict[str, Any] | ProvisionerError
) -> dict[str, Any] | None:
    if isinstance(out, ProvisionerError):
        log_event(log, "stats failed", database_id=str(d.id), code=out.code)
        return None
    m = out.get("measured") or {}
    now = utcnow()
    if out.get("state") != "running":
        sm.record_event(db, d, "health_check_failed", f"Instance state is {out.get('state')}")
        return m
    for metric, key in DB_METRICS.items():
        v = m.get(key)
        if v is not None:
            record(db, user_id=d.owner_id, project_id=d.project_id, database_id=d.id, metric=metric, quantity=v, at=now)
    record(
        db,
        user_id=d.owner_id,
        project_id=d.project_id,
        database_id=d.id,
        metric="allocated_storage_bytes",
        quantity=d.allocated_storage_mb * 1024 * 1024,
        source="allocated",
        at=now,
    )
    if m.get("database_size_bytes") is not None:
        d.storage_used_bytes = int(m["database_size_bytes"])
    d.connections_current = m.get("active_connections")
    if m.get("active_connections"):
        d.last_activity_at = now
    elif m.get("last_activity_at"):
        d.last_activity_at = dt.datetime.fromisoformat(m["last_activity_at"])
    d.metrics_measured_at = now
    _enforce_storage(db, prov, d, username, bool(m.get("read_only")))
    return m


def _enforce_storage(db: Session, prov: ProvisionerClient, d: Database, username: str, read_only: bool) -> None:
    if d.storage_used_bytes is None:
        return
    limit = d.allocated_storage_mb * 1024 * 1024
    ratio = d.storage_used_bytes / limit if limit else 0
    try:
        if ratio >= 1.0 and not read_only:
            prov.set_read_only(d.internal_database_name, database=d.pg_database_name, read_only=True)
            d.storage_over_limit = True
            sm.record_event(
                db, d, "storage_limit_reached", "Storage limit reached: new sessions default to read-only transactions"
            )
            notify(
                db,
                d.owner_id,
                "usage_threshold",
                f"“{d.name}” reached its storage limit",
                "New sessions are read-only by default until you free space or increase the allocation.",
                severity="error",
                link=f"/databases/{d.id}",
                dedupe_key=f"storage-full:{d.id}:{utcnow():%Y%m%d}",
            )
        elif ratio < 0.95 and read_only and d.storage_over_limit:
            prov.set_read_only(d.internal_database_name, database=d.pg_database_name, read_only=False)
            d.storage_over_limit = False
            sm.record_event(db, d, "storage_limit_cleared", "Storage below limit: writes re-enabled")
    except ProvisionerError as e:
        log_event(log, "storage enforcement failed", database_id=str(d.id), code=e.code)


def check_thresholds(db: Session, user: User) -> None:
    """Plan-level usage alerts (default 70/85/100%) for storage and database count."""
    ent = plans.entitlements_for(db, user.id)
    if ent.get("usage_alerts", "none") == "none":
        return
    thresholds = sorted((user.preferences or {}).get("usage_alert_thresholds") or [70, 85, 100])
    counts = plans.usage_counts(db, user.id)
    checks = {
        "storage": (counts["storage_used_bytes"], ent["max_total_storage_mb"] * 1024 * 1024, "storage"),
        "databases": (counts["databases"], ent["max_databases"], "databases"),
    }
    period = utcnow().strftime("%Y-%m")
    for key, (used, limit, label) in checks.items():
        if not limit:
            continue
        pct = used / limit * 100
        crossed = [t for t in thresholds if pct >= t]
        if crossed:
            t = crossed[-1]
            notify(
                db,
                user.id,
                "usage_threshold",
                f"You've used {t}% of your plan's {label}",
                f"Current usage is {pct:.0f}% of the {ent.plan_name} plan limit.",
                severity="warning" if t < 100 else "error",
                link="/usage",
                dedupe_key=f"threshold:{key}:{t}:{period}",
            )


def collect_all(db: Session, prov: ProvisionerClient | ProvisionerPool, parallelism: int = 8) -> int:
    """Measure every running database. Provisioner calls (the slow part: CPU usage takes a
    sampling interval) run in parallel across databases and hosts; results are applied here."""
    pool = prov if isinstance(prov, ProvisionerPool) else ProvisionerPool(override=prov)
    offline = set(
        db.scalars(select(InfrastructureHost.id).where(InfrastructureHost.status.in_(("offline", "retired")))).all()
    )
    tasks: list[tuple[Database, ProvisionerClient, str]] = []
    for d in db.scalars(select(Database).where(Database.status == "ready")).all():
        if d.host_id in offline:
            continue
        cred = _credential(db, d)
        if cred is None:
            continue
        try:
            tasks.append((d, pool.for_database(db, d), cred.username))
        except HostUnavailable:
            continue
    with ThreadPoolExecutor(max_workers=max(1, parallelism)) as ex:
        results = list(ex.map(lambda t: _fetch(t[1], t[0].internal_database_name, t[0].pg_database_name, t[2]), tasks))
    n = 0
    for (d, client, username), out in zip(tasks, results, strict=True):
        if _apply(db, client, d, username, out) is not None:
            n += 1
    db.flush()
    now = utcnow()
    # Account-level metrics (counts are measured from the control plane).
    rows = db.execute(select(User.id).where(User.status == "active", User.deleted_at.is_(None))).scalars().all()
    for uid in rows:
        counts = plans.usage_counts(db, uid)
        if counts["projects"] or counts["databases"]:
            record(db, user_id=uid, metric="projects", quantity=counts["projects"], at=now)
            record(db, user_id=uid, metric="databases", quantity=counts["databases"], at=now)
            record(
                db,
                user_id=uid,
                metric="allocated_storage_mb",
                quantity=counts["allocated_storage_mb"],
                source="allocated",
                at=now,
            )
            record(db, user_id=uid, metric="account_storage_bytes", quantity=counts["storage_used_bytes"], at=now)
            user = db.get(User, uid)
            if user:
                check_thresholds(db, user)
    return n


HOST_OFFLINE_AFTER = dt.timedelta(minutes=2)


def update_host_inventory(db: Session, prov: ProvisionerClient | ProvisionerPool) -> dict[str, int]:
    """Heartbeat every registered host (capacity call). A host that stops answering for
    HOST_OFFLINE_AFTER goes `offline` (audited, alert); it returns to its previous status
    (`active` or `draining`) when it answers again. Databases are never moved automatically.
    The bootstrap provisioner (PROVISIONER_URL) registers itself on first contact."""
    pool = prov if isinstance(prov, ProvisionerPool) else ProvisionerPool(override=prov)
    now = utcnow()
    stats = {"ok": 0, "unreachable": 0, "went_offline": 0}
    hosts = list(db.scalars(select(InfrastructureHost).where(InfrastructureHost.status != "retired")))
    if pool.override is not None or bootstrap_host(db) is None:
        _heartbeat_default(db, pool.default(), now)
        hosts = list(db.scalars(select(InfrastructureHost).where(InfrastructureHost.status != "retired")))
        if pool.override is not None:
            return stats
    for h in hosts:
        try:
            cap = pool.for_host(db, h).capacity()
        except (ProvisionerError, HostUnavailable) as e:
            stats["unreachable"] += 1
            h.last_error = (getattr(e, "code", None) or type(e).__name__)[:300]
            if h.status in ("active", "draining") and (
                h.last_heartbeat_at is None or now - h.last_heartbeat_at > HOST_OFFLINE_AFTER
            ):
                h.capacity_metadata = {**(h.capacity_metadata or {}), "_status_before_offline": h.status}
                h.status = "offline"
                h.status_reason = "No heartbeat from its provisioner"
                h.status_changed_at = now
                stats["went_offline"] += 1
                audit(
                    db,
                    "infrastructure.host_offline",
                    actor_type="system",
                    resource_type="host",
                    resource_id=h.id,
                    outcome="failure",
                    metadata={"host": h.name, "databases": _count_on_host(db, h.id)},
                )
            continue
        _record_heartbeat(db, h, cap, now)
        stats["ok"] += 1
    return stats


def _count_on_host(db: Session, host_id: uuid.UUID) -> int:
    return (
        db.scalar(select(func.count(Database.id)).where(Database.host_id == host_id, Database.status != "deleted")) or 0
    )


def _record_heartbeat(db: Session, h: InfrastructureHost, cap: dict[str, Any], now: dt.datetime) -> None:
    before = (h.capacity_metadata or {}).get("_status_before_offline")
    h.capacity_metadata = cap
    h.last_heartbeat_at = now
    h.last_error = None
    if h.status == "offline":
        h.status = before if before in ("active", "draining") else "active"
        h.status_reason = "Heartbeat restored"
        h.status_changed_at = now
        audit(
            db,
            "infrastructure.host_online",
            actor_type="system",
            resource_type="host",
            resource_id=h.id,
            metadata={"host": h.name},
        )


def _heartbeat_default(db: Session, prov: ProvisionerClient, now: dt.datetime) -> None:
    try:
        cap = prov.capacity()
    except ProvisionerError as e:
        log_event(log, "capacity unavailable", code=e.code)
        return
    host = db.scalar(select(InfrastructureHost).where(InfrastructureHost.name == cap["host_name"]))
    if host is None:
        host = InfrastructureHost(
            id=uuid.uuid4(),
            name=cap["host_name"],
            provider="systemd",
            region=cap["region"],
            endpoint=prov.base_url,
            status="active",
            labels=cap.get("labels") or {},
            status_changed_at=now,
        )
        db.add(host)
    _record_heartbeat(db, host, cap, now)
    db.flush()
    # Databases created before hosts were tracked live on the bootstrap host.
    # Instances created before hosts were tracked live on the bootstrap host (databases that were
    # never provisioned get placed normally instead).
    db.execute(
        text(
            "UPDATE databases SET host_id = :h WHERE host_id IS NULL AND status <> 'deleted' "
            "AND internal_endpoint_host IS NOT NULL"
        ),
        {"h": host.id},
    )


ROLLUP_SQL = {
    # Static statements (no interpolation): raw -> hourly, hourly -> daily averages.
    "raw": text(
        """
        INSERT INTO usage_records (id, user_id, project_id, database_id, metric, quantity, source, granularity,
                                   measured_at, period)
        SELECT gen_random_uuid(), user_id, project_id, database_id, metric, avg(quantity), min(source), 'hour',
               date_trunc('hour', measured_at), to_char(date_trunc('hour', measured_at), 'YYYY-MM')
        FROM usage_records WHERE granularity = 'raw' AND measured_at < :cutoff
        GROUP BY user_id, project_id, database_id, metric, date_trunc('hour', measured_at)
        """
    ),
    "hour": text(
        """
        INSERT INTO usage_records (id, user_id, project_id, database_id, metric, quantity, source, granularity,
                                   measured_at, period)
        SELECT gen_random_uuid(), user_id, project_id, database_id, metric, avg(quantity), min(source), 'day',
               date_trunc('day', measured_at), to_char(date_trunc('day', measured_at), 'YYYY-MM')
        FROM usage_records WHERE granularity = 'hour' AND measured_at < :cutoff
        GROUP BY user_id, project_id, database_id, metric, date_trunc('day', measured_at)
        """
    ),
}


def rollup_and_prune(db: Session) -> dict[str, int]:
    """Roll raw samples older than 7 days into hourly averages, hourly older than 90 days into
    daily averages, and delete beyond retention."""
    now = utcnow()
    out = {}
    for src_gran, age in (("raw", RAW_RETENTION), ("hour", HOURLY_RETENTION)):
        cutoff = now - age
        db.execute(ROLLUP_SQL[src_gran], {"cutoff": cutoff})
        r = db.execute(delete(UsageRecord).where(UsageRecord.granularity == src_gran, UsageRecord.measured_at < cutoff))
        out[src_gran] = int(r.rowcount or 0)  # type: ignore[attr-defined]
    r = db.execute(
        delete(UsageRecord).where(UsageRecord.granularity == "day", UsageRecord.measured_at < now - DAILY_RETENTION)
    )
    out["day"] = int(r.rowcount or 0)  # type: ignore[attr-defined]
    return out


def flush_api_counters(db: Session) -> int:
    """Move per-hour API request counts from Redis into api_request_counters."""
    import redis

    from app.core.redis import get_redis

    try:
        r = get_redis()
        keys = list(r.scan_iter(match="dmx:apicount:*", count=100))
    except redis.RedisError:
        return 0
    moved = 0
    current = utcnow().strftime("%Y%m%d%H")
    for key in keys:
        hour = key.rsplit(":", 1)[-1]
        tmp = f"{key}:flush:{uuid.uuid4().hex}"
        try:
            if not r.rename(key, tmp):
                continue
        except redis.ResponseError:
            continue
        data = r.hgetall(tmp)
        r.delete(tmp)
        ts = dt.datetime.strptime(hour, "%Y%m%d%H").replace(tzinfo=dt.UTC)
        for uid, cnt in data.items():
            uid = str(uid)
            if uid.startswith("k:"):
                try:
                    _, key_id, owner = uid.split(":", 2)
                    key_uuid, owner_uuid = uuid.UUID(key_id), uuid.UUID(owner)
                except ValueError:
                    continue
                db.execute(
                    text(
                        "INSERT INTO api_key_request_counters (api_key_id, hour, user_id, count) "
                        "VALUES (:k, :h, :u, :c) ON CONFLICT (api_key_id, hour) "
                        "DO UPDATE SET count = api_key_request_counters.count + EXCLUDED.count"
                    ),
                    {"k": key_uuid, "h": ts, "u": owner_uuid, "c": int(cnt)},
                )
                continue
            db.execute(
                text(
                    "INSERT INTO api_request_counters (user_id, hour, count) VALUES (:u, :h, :c) "
                    "ON CONFLICT (user_id, hour) DO UPDATE SET count = api_request_counters.count + EXCLUDED.count"
                ),
                {"u": uid, "h": ts, "c": int(cnt)},
            )
            moved += int(cnt)
        _ = current
    return moved


def api_requests(db: Session, user_id: uuid.UUID, since: dt.datetime) -> int:
    return int(
        db.scalar(
            select(func.coalesce(func.sum(ApiRequestCounter.count), 0)).where(
                ApiRequestCounter.user_id == user_id, ApiRequestCounter.hour >= since
            )
        )
        or 0
    )


def project_breakdown(db: Session, user_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = db.execute(
        select(
            Project.id,
            Project.name,
            func.count(Database.id),
            func.coalesce(func.sum(Database.allocated_storage_mb), 0),
            func.coalesce(func.sum(Database.storage_used_bytes), 0),
            func.coalesce(func.sum(Database.connections_current), 0),
        )
        .select_from(Project)
        .join(Database, (Database.project_id == Project.id) & (Database.status != "deleted"), isouter=True)
        .where(Project.owner_id == user_id, Project.deleted_at.is_(None))
        .group_by(Project.id, Project.name)
        .order_by(Project.name)
    ).all()
    return [
        {
            "project_id": r[0],
            "name": r[1],
            "databases": int(r[2] or 0),
            "allocated_storage_mb": int(r[3] or 0),
            "storage_used_bytes": int(r[4] or 0),
            "connections": int(r[5] or 0),
        }
        for r in rows
    ]

"""Reconciliation between the control plane's view and each host's data plane.

Per host (reachable ones only):
- Orphans: instances without a live database row, whose database is failed/deleted, or whose
  database now lives elsewhere (e.g. the old copy after a move) are destroyed after a grace
  period. Instances of a database that is being moved *to* this host are left alone.
- Drift: endpoint addresses are refreshed; ready databases whose instance vanished are flagged.
Across hosts:
- Stuck lifecycle states: in-flight/deleting/migrating databases without any queued/running
  job get one.
Databases on an unreachable host are never re-created elsewhere automatically.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import log_event
from app.models import Database, DatabaseEvent, InfrastructureHost, ProvisioningJob, utcnow
from app.provisioning import state_machine as sm
from app.provisioning.client import ProvisionerClient, ProvisionerError
from app.provisioning.hosts import HostUnavailable, ProvisionerPool, bootstrap_host
from app.services.audit import audit
from app.services.database_service import create_job, provision_kind

log = logging.getLogger("dmx.reconcile")


def _age(created: str | None, now: dt.datetime) -> dt.timedelta | None:
    if not created:
        return None
    try:
        return now - dt.datetime.fromisoformat(created.replace("Z", "+00:00")[:26] + "+00:00")
    except ValueError:
        return None


def _has_live_job(db: Session, database_id: uuid.UUID) -> bool:
    return (
        db.scalar(
            select(ProvisioningJob.id).where(
                ProvisioningJob.database_id == database_id, ProvisioningJob.status.in_(("queued", "running"))
            )
        )
        is not None
    )


def reconcile_host(
    db: Session,
    prov: ProvisionerClient,
    host: InfrastructureHost | None,
    dbs: dict[str, Database],
    stats: dict[str, int],
) -> bool:
    """Reconcile one host. Returns False when the host couldn't be reached."""
    s = get_settings()
    try:
        instances = prov.list_instances()
    except ProvisionerError as e:
        log_event(log, "reconcile skipped for host", host=host.name if host else "default", code=e.code)
        return False
    # Scratch restore-verification instances are managed (and swept) by the provisioner itself.
    by_iid = {i["instance_id"]: i for i in instances if i.get("purpose", "customer") == "customer"}
    try:
        cleaned = prov.cleanup_orphans()
        stats["orphan_volumes_removed"] += len(cleaned.get("removed_volumes", []))
    except ProvisionerError as e:
        log_event(log, "orphan volume cleanup failed", code=e.code)
    host_id = host.id if host else None
    now = utcnow()
    migrating_here = {
        d.id: d for d in dbs.values() if d.status == "migrating" and host_id and d.migration_target_host_id == host_id
    }
    for iid, inst in by_iid.items():
        d = dbs.get(iid)
        db_id = inst.get("database_id")
        incoming = db_id is not None and any(str(k) == db_id for k in migrating_here)
        elsewhere = d is not None and host_id is not None and d.host_id is not None and d.host_id != host_id
        orphan = (d is None and not incoming) or (d is not None and (d.status in ("deleted", "failed") or elsewhere))
        if not orphan:
            continue
        age = _age(inst.get("created"), now)
        if age is not None and age < dt.timedelta(seconds=s.orphan_grace_seconds):
            continue
        # A failed database with an in-flight retry is not an orphan.
        if d is not None and not elsewhere and _has_live_job(db, d.id):
            continue
        try:
            prov.destroy(iid)
            stats["orphans_removed"] += 1
            audit(
                db,
                "infrastructure.orphan_removed",
                actor_type="system",
                tenant_user_id=d.owner_id if d else None,
                resource_type="instance",
                resource_id=iid,
                metadata={
                    "database_status": d.status if d else "missing",
                    "host": host.name if host else None,
                    "reason": "database lives on another host" if elsewhere else "no live database",
                },
            )
        except ProvisionerError as e:
            log_event(log, "orphan removal failed", instance=iid, code=e.code)

    for d in dbs.values():
        if host is not None and d.host_id != host.id and not (d.host_id is None and _is_bootstrap(db, host)):
            continue
        if d.status != "ready":
            continue
        live_inst = by_iid.get(d.internal_database_name)
        if live_inst is None:
            stats["missing"] += 1
            recently_flagged = db.scalar(
                select(DatabaseEvent.id).where(
                    DatabaseEvent.database_id == d.id,
                    DatabaseEvent.event_type == "instance_missing",
                    DatabaseEvent.created_at > now - dt.timedelta(hours=1),
                )
            )
            if recently_flagged:
                continue  # already reported; the alert stays up via the admin infrastructure view
            sm.record_event(db, d, "instance_missing", "Instance not found on its host; operators alerted")
            audit(
                db,
                "infrastructure.instance_missing",
                actor_type="system",
                tenant_user_id=d.owner_id,
                resource_type="database",
                resource_id=d.id,
                outcome="failure",
            )
        elif live_inst.get("endpoint_host") and (
            live_inst["endpoint_host"] != d.internal_endpoint_host
            or int(live_inst.get("endpoint_port") or 5432) != (d.internal_endpoint_port or 5432)
        ):
            d.internal_endpoint_host = live_inst["endpoint_host"]
            d.internal_endpoint_port = int(live_inst.get("endpoint_port") or 5432)
            stats["endpoints_updated"] += 1
    return True


def _is_bootstrap(db: Session, host: InfrastructureHost) -> bool:
    b = bootstrap_host(db)
    return b is not None and b.id == host.id


def reconcile(db: Session, prov: ProvisionerClient | ProvisionerPool) -> dict[str, Any]:
    pool = prov if isinstance(prov, ProvisionerPool) else ProvisionerPool(override=prov)
    stats: dict[str, Any] = {
        "orphans_removed": 0,
        "jobs_created": 0,
        "endpoints_updated": 0,
        "missing": 0,
        "orphan_volumes_removed": 0,
        "hosts_unreachable": 0,
    }
    dbs = {d.internal_database_name: d for d in db.scalars(select(Database))}
    hosts = list(db.scalars(select(InfrastructureHost).where(InfrastructureHost.status != "retired")))
    if pool.override is not None or not hosts:
        reconcile_host(db, pool.default(), None, dbs, stats)
    else:
        for h in hosts:
            if h.status == "offline":
                stats["hosts_unreachable"] += 1
                continue
            try:
                client = pool.for_host(db, h)
            except HostUnavailable:
                stats["hosts_unreachable"] += 1
                continue
            if not reconcile_host(db, client, h, dbs, stats):
                stats["hosts_unreachable"] += 1

    now = utcnow()
    for d in dbs.values():
        stuck_state = d.status in sm.IN_FLIGHT or d.status in ("deleting", "stopping", "migrating")
        if stuck_state and d.updated_at < now - dt.timedelta(minutes=2) and not _has_live_job(db, d.id):
            last_kind = db.scalar(
                select(ProvisioningJob.kind)
                .where(ProvisioningJob.database_id == d.id)
                .order_by(ProvisioningJob.created_at.desc())
                .limit(1)
            )
            payload: dict[str, Any] = {"reconciled": True}
            if d.status == "migrating" or (d.status == "testing" and last_kind == "migrate"):
                kind = "migrate"
                last = db.scalar(
                    select(ProvisioningJob.payload)
                    .where(ProvisioningJob.database_id == d.id, ProvisioningJob.kind == "migrate")
                    .order_by(ProvisioningJob.created_at.desc())
                    .limit(1)
                )
                payload = {**(last or {}), "reconciled": True}  # keep progress markers
            else:
                kind = {"deleting": "delete", "stopping": "stop"}.get(d.status) or provision_kind(d)
            create_job(db, d, kind, payload=payload)
            sm.record_event(db, d, "reconciled", f"Re-queued {kind} after interruption")
            stats["jobs_created"] += 1
    return stats

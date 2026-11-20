"""Capacity- and constraint-aware placement of new instances on hosts.

A host is eligible when it is `active` (not draining/offline/retired), in the requested region,
has a fresh heartbeat, carries every required label, and still fits the new instance within
the budgets its provisioner reports (memory, CPU, disk, instance count). Allocations are
computed from the control plane's own records (every non-deleted database placed on the host),
so placements that the provisioner hasn't seen yet are counted too. Host rows are locked while
choosing, which serializes concurrent placements.

Among eligible hosts the one with the most headroom (smallest of the free memory / disk / CPU
fractions after placement) wins — spreading load and failure impact. The provisioner remains
authoritative: if it still reports `capacity_exceeded`, the job excludes that host and places
again (only possible before anything was created there).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Database, InfrastructureHost, utcnow
from app.provisioning.hosts import is_fresh
from app.services import plans

NOT_OCCUPYING = ("deleted", "failed")


class NoCapacity(Exception):
    pass


@dataclass
class Candidate:
    host: InfrastructureHost
    score: float
    detail: dict[str, Any]


def allocations(db: Session, host_id: uuid.UUID) -> dict[str, float]:
    rows = db.execute(
        select(
            Database.compute_profile, func.count(Database.id), func.coalesce(func.sum(Database.allocated_storage_mb), 0)
        )
        .where(Database.host_id == host_id, Database.status.notin_(NOT_OCCUPYING))
        .group_by(Database.compute_profile)
    ).all()
    incoming = db.execute(
        select(
            Database.compute_profile, func.count(Database.id), func.coalesce(func.sum(Database.allocated_storage_mb), 0)
        )
        .where(Database.migration_target_host_id == host_id, Database.status == "migrating")
        .group_by(Database.compute_profile)
    ).all()
    out = {"instances": 0.0, "memory_mb": 0.0, "cpus": 0.0, "storage_mb": 0.0}
    for profile, n, storage in [*rows, *incoming]:
        p = plans.PROFILES.get(profile, {})
        out["instances"] += n
        out["memory_mb"] += n * float(p.get("memory_mb", 0))
        out["cpus"] += n * float(p.get("cpus", 0))
        out["storage_mb"] += float(storage)
    return out


def evaluate(
    db: Session,
    host: InfrastructureHost,
    *,
    region: str,
    profile: str,
    storage_mb: int,
    labels: dict[str, str] | None = None,
) -> Candidate | str:
    """Returns a Candidate, or a short reason the host is not eligible."""
    now = utcnow()
    if host.status != "active":
        return f"host is {host.status}"
    if host.region != region:
        return "different region"
    if not is_fresh(host, now):
        return "no recent heartbeat"
    for k, v in (labels or {}).items():
        if str((host.labels or {}).get(k)) != str(v):
            return f"label {k} does not match"
    cap = host.capacity_metadata or {}
    budget_mem = float(cap.get("memory_budget_mb") or 0)
    budget_disk = float(cap.get("disk_budget_mb") or 0)
    budget_cpu = float(cap.get("cpu_budget") or 0)
    max_instances = float(cap.get("max_instances") or 0)
    if not budget_mem or not budget_disk or not max_instances:
        return "capacity unknown"
    if profile not in (cap.get("profiles") or plans.PROFILES):
        return "profile not offered"
    p = plans.PROFILES[profile]
    a = allocations(db, host.id)
    mem_after = a["memory_mb"] + float(p["memory_mb"])
    disk_after = a["storage_mb"] + storage_mb
    cpu_after = a["cpus"] + float(p["cpus"])
    if a["instances"] + 1 > max_instances:
        return "instance limit reached"
    if mem_after > budget_mem:
        return "memory budget exhausted"
    if disk_after > budget_disk:
        return "disk budget exhausted"
    fracs = [1 - mem_after / budget_mem, 1 - disk_after / budget_disk]
    if budget_cpu:
        fracs.append(max(0.0, 1 - cpu_after / budget_cpu))  # CPU is shared (soft); it only shapes the score
    return Candidate(
        host=host,
        score=min(fracs),
        detail={"allocated": a, "memory_after_mb": mem_after, "disk_after_mb": disk_after},
    )


def choose_host(
    db: Session,
    *,
    region: str,
    profile: str,
    storage_mb: int,
    exclude: set[uuid.UUID] | None = None,
    labels: dict[str, str] | None = None,
    only: uuid.UUID | None = None,
) -> InfrastructureHost:
    stmt = select(InfrastructureHost).where(InfrastructureHost.status == "active").order_by(InfrastructureHost.name)
    if only is not None:
        stmt = stmt.where(InfrastructureHost.id == only)
    hosts = db.scalars(stmt.with_for_update()).all()
    candidates: list[Candidate] = []
    reasons: dict[str, str] = {}
    for h in hosts:
        if exclude and h.id in exclude:
            reasons[h.name] = "excluded after a capacity error"
            continue
        r = evaluate(db, h, region=region, profile=profile, storage_mb=storage_mb, labels=labels)
        if isinstance(r, Candidate):
            candidates.append(r)
        else:
            reasons[h.name] = r
    if not candidates:
        raise NoCapacity("; ".join(f"{k}: {v}" for k, v in reasons.items()) or "no hosts registered")
    candidates.sort(key=lambda c: (-c.score, c.host.name))
    return candidates[0].host

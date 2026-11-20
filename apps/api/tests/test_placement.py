"""Capacity- and constraint-aware placement (no data plane needed)."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import delete, update

from app.core.db import system_session
from app.models import Database, InfrastructureHost, utcnow
from app.provisioning.placement import NoCapacity, allocations, choose_host

CAP = {
    "memory_budget_mb": 1024,
    "disk_budget_mb": 10240,
    "cpu_budget": 2.0,
    "max_instances": 10,
    "profiles": {"nano": {}, "small": {}, "medium": {}},
}


@pytest.fixture
def hosts():
    """Isolated registry for these tests (other hosts are parked as retired meanwhile)."""
    with system_session() as db:
        parked = [h.id for h in db.query(InfrastructureHost).filter(InfrastructureHost.status != "retired")]
        db.execute(update(InfrastructureHost).where(InfrastructureHost.id.in_(parked)).values(status="retired"))
    created: list[uuid.UUID] = []

    def make(name, *, status="active", region="oci-local-1", cap=None, beat_age=0, labels=None):
        with system_session() as db:
            h = InfrastructureHost(
                id=uuid.uuid4(), name=f"{name}-{uuid.uuid4().hex[:6]}", provider="systemd", region=region,
                endpoint=f"http://{name}.invalid:8090", status=status, capacity_metadata=CAP if cap is None else cap,
                labels=labels or {}, last_heartbeat_at=utcnow() - dt.timedelta(seconds=beat_age),
            )  # fmt: skip
            db.add(h)
            created.append(h.id)
            return h.id

    yield make
    with system_session() as db:
        db.execute(update(Database).where(Database.host_id.in_(created)).values(host_id=None))
        db.execute(delete(InfrastructureHost).where(InfrastructureHost.id.in_(created)))
        db.execute(update(InfrastructureHost).where(InfrastructureHost.id.in_(parked)).values(status="active"))


def choose(**kw):
    with system_session() as db:
        return choose_host(db, region=kw.pop("region", "oci-local-1"), profile=kw.pop("profile", "nano"),
                           storage_mb=kw.pop("storage_mb", 512), **kw).id  # fmt: skip


def fill(host_id, user, project, n, profile="nano", storage=500):
    for i in range(n):
        r = user.post("/databases", json={"project_id": project["id"], "name": f"f{i}-{uuid.uuid4().hex[:4]}"})
        assert r.status_code == 202, r.text
        with system_session() as db:
            db.execute(
                update(Database)
                .where(Database.id == uuid.UUID(r.json()["id"]))
                .values(host_id=host_id, compute_profile=profile, allocated_storage_mb=storage, status="ready")
            )


def test_ineligible_hosts_are_skipped(hosts):
    hosts("draining", status="draining")
    hosts("offline", status="offline")
    hosts("stale", beat_age=600)
    hosts("elsewhere", region="oci-other-1")
    hosts("unknown", cap={})
    with pytest.raises(NoCapacity) as ei:
        choose()
    msg = str(ei.value)
    assert "no recent heartbeat" in msg and "different region" in msg and "capacity unknown" in msg
    ok = hosts("ok")
    assert choose() == ok


def test_spreads_to_the_host_with_most_headroom(hosts, user, project):
    from tests.test_backups import set_plan

    set_plan(user.user["id"], "pro")
    busy, idle = hosts("busy"), hosts("idle")
    fill(busy, user, project, 2)
    assert choose() == idle
    with system_session() as db:
        a = allocations(db, busy)
    assert a["instances"] == 2 and a["memory_mb"] == 512 and a["storage_mb"] == 1000


def test_budgets_labels_and_exclusions(hosts, user, project):
    from tests.test_backups import set_plan

    set_plan(user.user["id"], "pro")
    small = hosts("small", cap={**CAP, "memory_budget_mb": 512})
    fill(small, user, project, 2)  # 2 x 256 MB = full
    with pytest.raises(NoCapacity, match="memory budget exhausted"):
        choose()
    big = hosts("big", labels={"tier": "fast"})
    assert choose(labels={"tier": "fast"}) == big
    with pytest.raises(NoCapacity, match="label tier"):
        choose(labels={"tier": "slow"})
    with pytest.raises(NoCapacity):
        choose(exclude={big})
    with pytest.raises(NoCapacity, match="disk budget"):
        choose(storage_mb=20000)

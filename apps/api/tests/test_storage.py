"""Storage allocation changes are applied to the data plane (hard-limited volumes grow)."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.core.db import system_session
from app.models import DatabaseEvent, ProvisioningJob
from app.workers import jobs
from tests.test_backups import force_ready, make_db, set_plan


class Prov:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def set_storage(self, iid: str, *, storage_mb: int) -> dict:
        self.calls.append((iid, storage_mb))
        return {"backend": "loop", "hard_limit": True, "changed": True, "mode": "online"}


def test_storage_change_requires_settled_state(user, project):
    d = make_db(user, project)  # still queued
    r = user.patch(f"/databases/{d['id']}", json={"storage_mb": 300})
    assert r.status_code == 409 and r.json()["error"]["code"] == "invalid_state"


def test_storage_increase_is_applied_by_a_job(user, project):
    set_plan(user.user["id"], "developer")
    d = make_db(user, project)
    force_ready(d["id"])
    r = user.patch(f"/databases/{d['id']}", json={"storage_mb": 2048})
    assert r.status_code == 200, r.text
    with system_session() as db:
        job = db.scalar(
            select(ProvisioningJob).where(
                ProvisioningJob.database_id == uuid.UUID(d["id"]), ProvisioningJob.status == "queued"
            )
        )
        assert job.kind == "rotate" and job.payload == {"action": "storage"}
    prov = Prov()
    jid = jobs.claim_next("t")
    assert jobs.execute(jid, "t", prov) == "succeeded"  # type: ignore[arg-type]
    assert prov.calls and prov.calls[0][1] == 2048
    with system_session() as db:
        ev = db.scalar(
            select(DatabaseEvent).where(
                DatabaseEvent.database_id == uuid.UUID(d["id"]), DatabaseEvent.event_type == "storage_resized"
            )
        )
        assert ev is not None and "2048 MB" in ev.message

"""Backups, retention, scheduling and clones — control-plane behaviour (no data plane needed).
End-to-end data tests live in test_backups_integration.py."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select, update

from app.core.db import system_session
from app.models import Backup, Database, Notification, ProvisioningJob, Subscription, utcnow
from app.provisioning.client import ProvisionerError
from app.services import backup_service
from app.workers import jobs


def set_plan(user_id: str, plan: str) -> None:
    with system_session() as db:
        db.execute(update(Subscription).where(Subscription.user_id == uuid.UUID(user_id)).values(plan=plan))


def make_db(user, project, name="db1") -> dict:
    r = user.post("/databases", json={"project_id": project["id"], "name": name})
    assert r.status_code == 202, r.text
    return r.json()


def force_ready(database_id: str, age_minutes: int = 120) -> None:
    """Pretend provisioning finished (these tests don't touch the data plane)."""
    with system_session() as db:
        db.execute(
            update(ProvisioningJob)
            .where(ProvisioningJob.database_id == uuid.UUID(database_id), ProvisioningJob.status == "queued")
            .values(status="cancelled")
        )
        db.execute(
            update(Database)
            .where(Database.id == uuid.UUID(database_id))
            .values(status="ready", ready_at=utcnow() - dt.timedelta(minutes=age_minutes))
        )


def complete_backup(backup_id: str, *, days_ago: float = 0, retention_days: int = 7) -> None:
    done = utcnow() - dt.timedelta(days=days_ago)
    with system_session() as db:
        b = db.get(Backup, uuid.UUID(backup_id))
        b.status = "completed"
        b.size = 12345
        b.checksum_sha256 = "a" * 64
        b.table_count = 3
        b.completed_at = done
        b.created_at = done
        b.expires_at = done + dt.timedelta(days=retention_days)
        db.execute(
            update(ProvisioningJob)
            .where(ProvisioningJob.kind == "backup", ProvisioningJob.status.in_(("queued", "running")))
            .values(status="succeeded")
        )


def test_backup_requires_running_database(user, project):
    d = make_db(user, project)
    r = user.post(f"/databases/{d['id']}/backups")
    assert r.status_code == 409 and r.json()["error"]["code"] == "invalid_state"


def test_manual_backup_flow_and_daily_limit(user, project):
    d = make_db(user, project)
    force_ready(d["id"])
    r = user.post(f"/databases/{d['id']}/backups")
    assert r.status_code == 202, r.text
    b = r.json()
    assert b["kind"] == "manual" and b["status"] == "pending" and b["restorable"] is False
    # One at a time per database.
    r = user.post(f"/databases/{d['id']}/backups")
    assert r.status_code == 409 and r.json()["error"]["code"] == "backup_in_progress"
    complete_backup(b["id"])
    # Free plan: 1 on-demand backup per database per day.
    r = user.post(f"/databases/{d['id']}/backups")
    assert r.status_code == 402 and r.json()["error"]["code"] == "quota_exceeded"
    listed = user.get(f"/databases/{d['id']}/backups").json()
    assert listed["total"] == 1 and listed["items"][0]["restorable"] is True
    summary = user.get(f"/databases/{d['id']}/backups/summary").json()
    assert summary["retention_days"] == 3 and summary["manual_backups_today"] == 1
    assert summary["count"] == 1 and summary["total_size_bytes"] == 12345
    assert user.get(f"/backups/{b['id']}").json()["id"] == b["id"]
    dash = user.get("/dashboard/summary").json()
    assert dash["backups"]["count"] == 1 and dash["backups"]["stored_bytes"] == 12345


def test_backups_are_tenant_isolated(make_user):
    a, b = make_user(), make_user()
    pa = a.post("/projects", json={"name": "A"}).json()
    d = make_db(a, pa)
    force_ready(d["id"])
    bk = a.post(f"/databases/{d['id']}/backups").json()
    complete_backup(bk["id"])
    assert b.get(f"/backups/{bk['id']}").status_code == 404
    assert b.get(f"/databases/{d['id']}/backups").status_code == 404
    assert b.post(f"/databases/{d['id']}/backups").status_code == 404
    assert b.post(f"/databases/{d['id']}/clone", json={"name": "steal", "backup_id": bk["id"]}).status_code == 404
    assert b.get("/backups").json()["total"] == 0


def test_backup_scopes_for_api_keys(user, project):
    d = make_db(user, project)
    force_ready(d["id"])
    k = user.post("/api-keys", json={"name": "ro", "scopes": ["databases:read"]}).json()
    h = {"Authorization": f"Bearer {k['key']}"}
    r = user.c.get(f"/api/v1/databases/{d['id']}/backups", headers=h)
    assert r.status_code == 403
    k2 = user.post("/api-keys", json={"name": "bk", "scopes": ["backups:read", "backups:write"]}).json()
    h2 = {"Authorization": f"Bearer {k2['key']}"}
    assert user.c.get(f"/api/v1/databases/{d['id']}/backups", headers=h2).status_code == 200
    assert user.c.post(f"/api/v1/databases/{d['id']}/backups", headers=h2).status_code == 202
    # Cloning needs both databases:write and backups:write.
    r = user.c.post(f"/api/v1/databases/{d['id']}/clone", json={"name": "c"}, headers=h2)
    assert r.status_code == 403


def test_clone_respects_plan_quota_and_validation(user, project):
    d = make_db(user, project)
    force_ready(d["id"])
    r = user.post(f"/databases/{d['id']}/clone", json={"name": "copy"})
    assert r.status_code == 402  # free plan: 1 database
    set_plan(user.user["id"], "developer")
    r = user.post(f"/databases/{d['id']}/clone", json={"name": "db1"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "name_taken"
    r = user.post(f"/databases/{d['id']}/clone", json={"name": "copy", "backup_id": str(uuid.uuid4())})
    assert r.status_code == 404
    r = user.post(f"/databases/{d['id']}/clone", json={"name": "copy"})
    assert r.status_code == 202, r.text
    c = r.json()
    assert c["source_database_id"] == d["id"] and c["status"] == "queued"
    assert c["database_name"] == d["database_name"]  # same database name inside, new host/user/password
    with system_session() as db:
        job = db.scalar(select(ProvisioningJob).where(ProvisioningJob.database_id == uuid.UUID(c["id"])))
        assert job.kind == "clone"
    # A second copy of current data while the pre-clone backup hasn't started is still allowed,
    # but the source must be running.
    with system_session() as db:
        db.execute(update(Database).where(Database.id == uuid.UUID(d["id"])).values(status="stopped"))
    r = user.post(f"/databases/{d['id']}/clone", json={"name": "copy2"})
    assert r.status_code == 409


def test_expired_backup_is_not_restorable(user, project):
    set_plan(user.user["id"], "developer")
    d = make_db(user, project)
    force_ready(d["id"])
    bk = user.post(f"/databases/{d['id']}/backups").json()
    complete_backup(bk["id"], days_ago=10, retention_days=7)
    r = user.post(f"/databases/{d['id']}/clone", json={"name": "old", "backup_id": bk["id"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_restorable"


def test_scheduler_queues_daily_backups_once(user, project):
    d = make_db(user, project)
    force_ready(d["id"])
    with system_session() as db:
        n1 = backup_service.schedule_daily_backups(db)
    with system_session() as db:
        n2 = backup_service.schedule_daily_backups(db)
        rows = db.scalars(select(Backup).where(Backup.database_id == uuid.UUID(d["id"]))).all()
    assert n1 >= 1 and n2 == 0
    assert [r.kind for r in rows] == ["scheduled"]
    # A database that became ready moments ago waits (avoids backing up during first use).
    with system_session() as db:
        db.execute(update(Database).where(Database.id == uuid.UUID(d["id"])).values(ready_at=utcnow()))
    complete_backup(str(rows[0].id), days_ago=2)
    with system_session() as db:
        assert backup_service.schedule_daily_backups(db) == 0


class FakeProv:
    def __init__(self, fail: ProvisionerError | None = None):
        self.fail = fail
        self.deleted: list[tuple[str, str]] = []

    def delete_backup(self, database_id, backup_id):
        self.deleted.append((database_id, backup_id))
        return {"status": "deleted"}

    def create_backup(self, iid, **kw):
        if self.fail:
            raise self.fail
        return {"storage_reference": "local:x", "size": 10, "sha256": "b" * 64, "key_id": "dev",
                "table_count": 1, "duration_ms": 5, "store": "local"}  # fmt: skip


def test_retention_expires_objects_and_rows(user, project):
    d = make_db(user, project)
    force_ready(d["id"])
    bk = user.post(f"/databases/{d['id']}/backups").json()
    complete_backup(bk["id"], days_ago=5, retention_days=3)
    prov = FakeProv()
    with system_session() as db:
        stats = backup_service.expire_backups(db, prov)  # type: ignore[arg-type]
    assert stats["expired"] >= 1
    assert (d["id"], bk["id"]) in prov.deleted
    assert user.get(f"/backups/{bk['id']}").json()["status"] == "expired"
    assert user.get(f"/databases/{d['id']}/backups").json()["total"] == 0  # expired hidden by default


def test_backup_job_success_and_failure_paths(user, project):
    d = make_db(user, project)
    force_ready(d["id"])
    bk = user.post(f"/databases/{d['id']}/backups").json()
    jid = jobs.claim_next("t")
    assert jobs.execute(jid, "t", FakeProv()) == "succeeded"  # type: ignore[arg-type]
    got = user.get(f"/backups/{bk['id']}").json()
    assert got["status"] == "completed" and got["size_bytes"] == 10 and got["table_count"] == 1
    assert got["expires_at"] is not None
    with system_session() as db:
        db.execute(
            update(Backup).where(Backup.id == uuid.UUID(bk["id"])).values(created_at=utcnow() - dt.timedelta(days=2))
        )

    with system_session() as db:
        database = db.get(Database, uuid.UUID(d["id"]))
        b2, _ = backup_service.request_backup(db, database, kind="scheduled")
        b2_id = str(b2.id)
    jid = jobs.claim_next("t")
    bad = ProvisionerError("invalid_request", "nope", status=422, retryable=False)
    assert jobs.execute(jid, "t", FakeProv(fail=bad)) == "failed"  # type: ignore[arg-type]
    got = user.get(f"/backups/{b2_id}").json()
    assert got["status"] == "failed" and got["error_message"]
    with system_session() as db:
        n = db.scalar(
            select(Notification).where(
                Notification.user_id == uuid.UUID(user.user["id"]), Notification.type == "backup_failed"
            )
        )
        assert n is not None and n.severity == "error"


def test_delete_with_final_backup_requires_running(user, project):
    d = make_db(user, project)
    r = user.delete(f"/databases/{d['id']}", params={"confirm": d["name"], "final_backup": "true"})
    assert r.status_code == 409


def test_admin_backup_endpoints(user, project, make_user):
    from tests.test_admin import _make_admin as make_admin

    d = make_db(user, project)
    force_ready(d["id"])
    bk = user.post(f"/databases/{d['id']}/backups").json()
    complete_backup(bk["id"])
    admin = make_admin(make_user)
    r = admin.get("/admin/backups")
    assert r.status_code == 200, r.text
    body = r.json()
    assert any(i["id"] == bk["id"] for i in body["items"])
    assert "ready_databases_with_recent_backup" in body["overview"]
    assert "stored_bytes" in admin.get("/admin/overview").json()["backups"]
    r = admin.post(f"/admin/backups/{bk['id']}/verify", json={"reason": "spot check"})
    assert r.status_code == 200, r.text
    assert user.get("/admin/backups").status_code == 404  # admin surface is hidden from non-admins


def test_suspended_database_cannot_be_cloned(user, project):
    set_plan(user.user["id"], "developer")
    d = make_db(user, project)
    force_ready(d["id"])
    bk = user.post(f"/databases/{d['id']}/backups").json()
    complete_backup(bk["id"])
    with system_session() as db:
        db.execute(update(Database).where(Database.id == uuid.UUID(d["id"])).values(suspended_by_admin=True))
    for body in ({"name": "escape"}, {"name": "escape", "backup_id": bk["id"]}):
        r = user.post(f"/databases/{d['id']}/clone", json=body)
        assert r.status_code == 409 and r.json()["error"]["code"] == "suspended"


def test_user_backup_allowance_and_delete(user, project):
    set_plan(user.user["id"], "developer")
    d = make_db(user, project)
    force_ready(d["id"])
    bk = user.post(f"/databases/{d['id']}/backups").json()
    complete_backup(bk["id"])
    with system_session() as db:  # pretend it is huge: 10 GB = the Developer allowance
        db.execute(update(Backup).where(Backup.id == uuid.UUID(bk["id"])).values(size=10240 * 2**20))
    r = user.post(f"/databases/{d['id']}/backups")
    assert r.status_code == 402 and r.json()["error"]["details"]["resource"] == "backup_storage"
    r = user.post(f"/databases/{d['id']}/clone", json={"name": "copy"})
    assert r.status_code == 402
    # Deleting the backup frees the allowance; the object is removed by the expiry task.
    r = user.delete(f"/backups/{bk['id']}")
    assert r.status_code == 200 and r.json()["status"] == "deleted"
    assert user.get(f"/backups/{bk['id']}").status_code == 404
    assert user.post(f"/databases/{d['id']}/backups").status_code == 202
    with system_session() as db:
        db.execute(update(Backup).where(Backup.id == uuid.UUID(bk["id"])).values(storage_reference="local:x"))
    prov = FakeProv()
    with system_session() as db:
        backup_service.expire_backups(db, prov)  # type: ignore[arg-type]
    assert (d["id"], bk["id"]) in prov.deleted


def test_failed_clone_resets_restore_marker(user, project):
    from app.workers.jobs import JobFailed, _handle_failure

    set_plan(user.user["id"], "developer")
    d = make_db(user, project)
    force_ready(d["id"])
    c = user.post(f"/databases/{d['id']}/clone", json={"name": "copy"}).json()
    with system_session() as db:
        db.execute(update(Database).where(Database.id == uuid.UUID(c["id"])).values(data_restored_at=utcnow()))
        job = db.scalar(select(ProvisioningJob).where(ProvisioningJob.database_id == uuid.UUID(c["id"])))
        job.status, job.attempt_count, job.max_attempts = "running", 3, 3
        jid = job.id

    class NoProv:
        def destroy(self, iid):
            return {}

    from app.provisioning.hosts import ProvisionerPool

    assert _handle_failure(jid, JobFailed("x", "boom", retryable=False), ProvisionerPool(override=NoProv())) == "failed"  # type: ignore[arg-type]
    with system_session() as db:
        assert db.get(Database, uuid.UUID(c["id"])).data_restored_at is None

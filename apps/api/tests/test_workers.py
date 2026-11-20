"""Worker internals: reconciliation, outbox delivery, notification dedupe, usage endpoints."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select, update

from app.core.db import system_session
from app.models import Database, EmailOutbox, Notification, ProvisioningJob
from app.services import email
from app.services.notifications import notify
from app.workers.reconcile import reconcile


class FakeProv:
    base_url = "fake"

    def __init__(self, instances):
        self.instances = instances
        self.destroyed = []

    def list_instances(self):
        return self.instances

    def cleanup_orphans(self):
        self.cleanups = getattr(self, "cleanups", 0) + 1
        return {"removed_volumes": ["abc-orphan-wal"], "removed_verify_instances": 0}

    def destroy(self, iid):
        self.destroyed.append(iid)
        return {"removed": True}


def test_reconcile_removes_orphans_and_requeues_stuck(user, project):
    d = user.post("/databases", json={"project_id": project["id"], "name": "stuck"}).json()
    old = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)).isoformat()
    with system_session() as db:
        # Simulate: job lost (cancelled) while database is mid-provisioning.
        db.execute(update(ProvisioningJob).where(ProvisioningJob.database_id == d["id"]).values(status="cancelled"))
        db.execute(
            update(Database)
            .where(Database.id == d["id"])
            .values(status="provisioning", updated_at=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=10))
        )
        iid = db.get(Database, d["id"]).internal_database_name
    prov = FakeProv(
        [
            {"instance_id": "orphan00000000000001", "created": old, "endpoint_host": "x"},  # no DB row at all
            {"instance_id": iid, "created": old, "endpoint_host": "x"},  # belongs to a live database
        ]
    )
    with system_session() as db:
        stats = reconcile(db, prov)  # type: ignore[arg-type]
    assert prov.destroyed == ["orphan00000000000001"]
    assert stats["orphans_removed"] == 1 and stats["jobs_created"] == 1
    with system_session() as db:
        assert (
            db.scalar(
                select(ProvisioningJob).where(
                    ProvisioningJob.database_id == d["id"], ProvisioningJob.status == "queued"
                )
            )
            is not None
        )


def test_reconcile_respects_grace_period():
    recent = dt.datetime.now(dt.UTC).isoformat()
    prov = FakeProv([{"instance_id": "recent0000000000001", "created": recent}])
    with system_session() as db:
        reconcile(db, prov)  # type: ignore[arg-type]
    assert prov.destroyed == []


def test_notification_dedupe_prevents_duplicate_emails(user):
    uid = uuid.UUID(user.user["id"])
    with system_session() as db:
        for _ in range(3):
            notify(db, uid, "database_ready", "Ready", "x", dedupe_key="same-key")
    with system_session() as db:
        n = db.scalars(
            select(Notification).where(Notification.user_id == uid, Notification.dedupe_key == "same-key")
        ).all()
        emails = db.scalars(select(EmailOutbox).where(EmailOutbox.dedupe_key == f"notif:{uid}:same-key")).all()
    assert len(n) == 1 and len(emails) == 1


def test_outbox_delivery_and_body_destroyed(make_user):
    make_user(verified=False)
    with system_session() as db:
        email.deliver_pending(db, limit=100)
        rows = db.scalars(select(EmailOutbox)).all()
        assert all(r.status == "sent" for r in rows)
        from app.models import Secret

        for r in rows:
            s = db.get(Secret, r.body_secret_id)
            assert s.destroyed_at is not None


def test_usage_and_dashboard_endpoints(user, project):
    user.post("/databases", json={"project_id": project["id"], "name": "u1"})
    summary = user.get("/dashboard/summary").json()
    assert summary["counts"]["databases"] == 1 and summary["counts"]["provisioning"] == 1
    assert summary["quotas"]["plan"]["id"] == "free"
    q = {i["resource"]: i for i in user.get("/usage/quotas").json()["items"]}
    assert q["databases"]["used"] == 1 and q["databases"]["percent"] == 100.0
    assert q["storage_used"]["kind"] == "measured" and q["allocated_storage"]["kind"] == "allocated"
    assert user.get("/usage/history", params={"metric": "api_requests"}).status_code == 200
    est = user.get("/usage/estimate").json()
    assert est["amount_cents"] == 0 and est["kind"] == "estimated"


def test_notifications_api(user):
    uid = uuid.UUID(user.user["id"])
    with system_session() as db:
        notify(db, uid, "plan_changed", "Hello", "World", email=False)
    assert user.get("/notifications/unread-count").json()["unread"] >= 1
    assert user.post("/notifications/read-all").status_code == 200
    assert user.get("/notifications/unread-count").json()["unread"] == 0


def test_essential_notifications_cannot_be_disabled(user):
    r = user.patch(
        "/account", json={"preferences": {"email_notifications": {"security_login": False, "database_ready": False}}}
    )
    assert r.status_code == 200
    settings = {s["type"]: s for s in user.get("/account/notification-settings").json()}
    assert settings["security_login"]["email"] is True and settings["database_ready"]["email"] is False

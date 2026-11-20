"""Documented billing policy enforcement (see docs/architecture/billing-policy.md).

Never deletes customer data. When a paid plan lapses (canceled at period end, or past_due beyond
the grace period) the account falls back to Free limits:
  day 0      notify: resources above Free limits will be stopped after the grace period
  day grace  stop the newest databases above the Free database/storage limits (not deleted)
Deletion of stopped resources only happens through an explicit support/admin action after
further notice; it is not automated.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Database, Plan, Subscription, utcnow
from app.provisioning import state_machine as sm
from app.services import plans
from app.services.audit import audit
from app.services.database_service import create_job
from app.services.notifications import notify


def enforce(db: Session) -> int:
    free = db.get(Plan, "free")
    if free is None:
        return 0
    limits = free.entitlements
    stopped = 0
    grace = dt.timedelta(days=get_settings().billing_grace_days)
    for sub in db.scalars(select(Subscription).where(Subscription.plan != "free")):
        ent = plans.effective_entitlements(db, sub)
        if ent.plan_id != "free":
            continue
        lapsed_at = sub.period_end if sub.status == "canceled" else (sub.grace_until or sub.updated_at)
        if lapsed_at is None:
            continue
        live = db.scalars(
            select(Database)
            .where(Database.owner_id == sub.user_id, Database.status.in_(("ready", "stopped", "failed")))
            .order_by(Database.created_at)
        ).all()
        running = [d for d in live if d.status == "ready"]
        excess: list[Database] = []
        alloc = 0
        for i, d in enumerate(running):
            alloc += d.allocated_storage_mb
            if i >= limits["max_databases"] or alloc > limits["max_total_storage_mb"]:
                excess.append(d)
        if not excess:
            continue
        notify(
            db,
            sub.user_id,
            "plan_changed",
            "Some databases exceed the Free plan",
            f"{len(excess)} database(s) will be stopped (not deleted) on "
            f"{(lapsed_at + grace):%B %d, %Y} unless you resubscribe or delete resources.",
            severity="warning",
            link="/billing",
            dedupe_key=f"excess-warning:{sub.id}:{lapsed_at:%Y%m%d}",
        )
        if utcnow() < lapsed_at + grace:
            continue
        for d in excess:
            sm.transition(db, d, "stopping", reason="Stopped: exceeds Free plan limits after grace period")
            create_job(db, d, "stop", payload={"reason": "billing"})
            audit(
                db,
                "billing.database_stopped",
                actor_type="system",
                tenant_user_id=sub.user_id,
                resource_type="database",
                resource_id=d.id,
            )
            stopped += 1
    return stopped

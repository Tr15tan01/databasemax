from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import time
import uuid

import pytest

SECRET = "pdl_ntfset_test_secret_value_1234567890"


@pytest.fixture(autouse=True)
def paddle_env(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("PADDLE_PRICE_DEVELOPER", "pri_dev")
    monkeypatch.setenv("PADDLE_PRICE_PRO", "pri_pro")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _sign(body: bytes, ts: int | None = None, secret: str = SECRET) -> str:
    ts = ts or int(time.time())
    h1 = hmac.new(secret.encode(), f"{ts}:".encode() + body, hashlib.sha256).hexdigest()
    return f"ts={ts};h1={h1}"


def _event(
    user_id: str, etype="subscription.activated", status="active", price="pri_dev", event_id=None, occurred=None
):
    return {
        "event_id": event_id or f"evt_{uuid.uuid4().hex}",
        "event_type": etype,
        "occurred_at": (occurred or dt.datetime.now(dt.UTC)).isoformat(),
        "data": {
            "id": f"sub_{user_id[:8]}",
            "status": status,
            "customer_id": "ctm_1",
            "items": [{"price": {"id": price}}],
            "current_billing_period": {"starts_at": "2026-09-01T00:00:00Z", "ends_at": "2026-10-01T00:00:00Z"},
            "custom_data": {"dmx_user_id": user_id},
        },
    }


def _post(client, event, signature=None):
    body = json.dumps(event).encode()
    return client.c.post(
        "/api/v1/billing/webhooks/paddle",
        content=body,
        headers={"Paddle-Signature": signature or _sign(body), "Content-Type": "application/json"},
    )


def test_forged_webhook_rejected(user):
    ev = _event(user.user["id"])
    body = json.dumps(ev).encode()
    for sig in ("", "ts=1;h1=deadbeef", _sign(body, secret="wrong-secret"), _sign(body, ts=int(time.time()) - 3600)):
        r = user.c.post("/api/v1/billing/webhooks/paddle", content=body, headers={"Paddle-Signature": sig})
        assert r.status_code == 401
    assert user.get("/billing/subscription").json()["plan"] == "free"


def test_tampered_body_rejected(user):
    ev = _event(user.user["id"])
    sig = _sign(json.dumps(ev).encode())
    ev["data"]["items"][0]["price"]["id"] = "pri_pro"
    assert _post(user, ev, signature=sig).status_code == 401


def test_valid_webhook_upgrades_and_is_idempotent(user):
    ev = _event(user.user["id"])
    assert _post(user, ev).json()["status"] == "processed"
    assert _post(user, ev).json()["status"] == "duplicate"
    sub = user.get("/billing/subscription").json()
    assert sub["plan"] == "developer" and sub["effective_plan"] == "developer"
    assert user.get("/databases/options").json()["max_connections_per_db"] == 50


def test_canceled_subscription_falls_back_to_free_limits(user):
    _post(user, _event(user.user["id"]))
    _post(user, _event(user.user["id"], etype="subscription.canceled", status="canceled"))
    sub = user.get("/billing/subscription").json()
    assert sub["status"] == "canceled" and sub["effective_plan"] == "free"
    assert user.get("/usage/quotas").json()["limits"]["max_databases"] == 1


def test_out_of_order_events_ignored(user):
    now = dt.datetime.now(dt.UTC)
    _post(user, _event(user.user["id"], etype="subscription.canceled", status="canceled", occurred=now))
    _post(user, _event(user.user["id"], status="active", occurred=now - dt.timedelta(minutes=5)))
    assert user.get("/billing/subscription").json()["status"] == "canceled"


def test_past_due_grace_period(user):
    _post(user, _event(user.user["id"]))
    _post(user, _event(user.user["id"], etype="subscription.past_due", status="past_due"))
    sub = user.get("/billing/subscription").json()
    assert sub["status"] == "past_due" and sub["effective_plan"] == "developer" and sub["grace_until"]
    from sqlalchemy import update

    from app.core.db import system_session
    from app.models import Subscription

    with system_session() as db:
        db.execute(
            update(Subscription)
            .where(Subscription.user_id == user.user["id"])
            .values(grace_until=dt.datetime.now(dt.UTC) - dt.timedelta(days=1))
        )
    assert user.get("/billing/subscription").json()["effective_plan"] == "free"


def test_frontend_checkout_success_does_not_grant_plan(user):
    # There is no endpoint that accepts "payment succeeded" from the browser; checkout without
    # Paddle configured is refused and the plan is unchanged.
    r = user.post("/billing/checkout", json={"plan": "pro"})
    assert r.status_code == 503
    assert user.get("/billing/subscription").json()["plan"] == "free"


def test_payment_failed_records_and_notifies(user):
    _post(user, _event(user.user["id"]))
    ev = {
        "event_id": "evt_payfail_1",
        "event_type": "transaction.payment_failed",
        "occurred_at": dt.datetime.now(dt.UTC).isoformat(),
        "data": {
            "id": "txn_1",
            "status": "past_due",
            "subscription_id": f"sub_{user.user['id'][:8]}",
            "customer_id": "ctm_1",
            "currency_code": "USD",
            "details": {"totals": {"grand_total": "900"}},
        },
    }
    assert _post(user, ev).json()["status"] == "processed"
    pays = user.get("/billing/payments").json()
    assert pays[0]["status"] == "failed" and pays[0]["amount_cents"] == 900
    notes = user.get("/notifications").json()["items"]
    assert any(n["type"] == "payment_failed" for n in notes)


def test_billing_enforcement_never_deletes(make_user):
    """A lapsed paid plan stops (never deletes) databases above Free limits after the grace period."""
    from sqlalchemy import select, update

    from app.billing.enforcement import enforce
    from app.core.db import system_session
    from app.models import Database, Subscription

    u = make_user()
    with system_session() as db:
        db.execute(update(Subscription).where(Subscription.user_id == u.user["id"]).values(plan="developer"))
    p = u.post("/projects", json={"name": "p"}).json()
    ids = [
        u.post("/databases", json={"project_id": p["id"], "name": f"d{i}", "storage_mb": 256}).json()["id"]
        for i in range(3)
    ]
    with system_session() as db:
        db.execute(update(Database).where(Database.id.in_(ids)).values(status="ready"))
        db.execute(
            update(Subscription)
            .where(Subscription.user_id == u.user["id"])
            .values(status="canceled", period_end=dt.datetime.now(dt.UTC) - dt.timedelta(days=30))
        )
    with system_session() as db:
        stopped = enforce(db)
    assert stopped == 2
    with system_session() as db:
        statuses = sorted(db.scalars(select(Database.status).where(Database.id.in_(ids))))
    assert statuses == ["ready", "stopping", "stopping"]
    assert "deleted" not in statuses

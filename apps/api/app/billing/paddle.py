"""Paddle Billing integration.

- Checkout: the backend creates a Paddle transaction for an internal plan (price ids are mapped
  server-side from PADDLE_PRICE_*), tagged with custom_data.dmx_user_id; the browser opens it
  with Paddle.js. The frontend's "success" is never trusted — only verified webhooks and
  reconciliation change entitlements.
- Webhooks: `Paddle-Signature: ts=<unix>;h1=<hex>` = HMAC-SHA256(secret, f"{ts}:{raw body}").
  Verified in constant time with a timestamp tolerance; processed idempotently by event id.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import time
import uuid
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ApiError
from app.models import BillingEvent, Payment, Subscription, User, utcnow
from app.services.audit import audit
from app.services.notifications import notify

SIGNATURE_TOLERANCE_SECONDS = 300


class SignatureInvalid(Exception):
    pass


def api_base() -> str:
    return (
        "https://sandbox-api.paddle.com" if get_settings().paddle_environment == "sandbox" else "https://api.paddle.com"
    )


def price_to_plan() -> dict[str, str]:
    s = get_settings()
    out = {}
    for plan, pid in (
        ("developer", s.paddle_price_developer),
        ("pro", s.paddle_price_pro),
        ("free", s.paddle_price_free),
    ):
        if pid:
            out[pid] = plan
    return out


def plan_to_price(plan: str) -> str | None:
    s = get_settings()
    return {"developer": s.paddle_price_developer, "pro": s.paddle_price_pro}.get(plan)


def verify_signature(raw_body: bytes, header: str | None, *, now: float | None = None) -> None:
    secret = get_settings().paddle_webhook_secret
    if secret is None:
        raise SignatureInvalid("webhook secret not configured")
    if not header:
        raise SignatureInvalid("missing signature")
    parts: dict[str, list[str]] = {}
    for item in header.split(";"):
        k, _, v = item.strip().partition("=")
        parts.setdefault(k, []).append(v)
    try:
        ts = int(parts["ts"][0])
    except (KeyError, ValueError, IndexError) as e:
        raise SignatureInvalid("malformed signature") from e
    if abs((now or time.time()) - ts) > SIGNATURE_TOLERANCE_SECONDS:
        raise SignatureInvalid("signature timestamp outside tolerance")
    expected = hmac.new(secret.get_secret_value().encode(), f"{ts}:".encode() + raw_body, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, h) for h in parts.get("h1", [])):
        raise SignatureInvalid("signature mismatch")


def _client() -> httpx.Client:
    s = get_settings()
    if not s.paddle_api_key:
        raise ApiError("Billing is not configured on this deployment.", code="billing_not_configured", status_code=503)
    return httpx.Client(
        base_url=api_base(),
        timeout=15,
        headers={"Authorization": f"Bearer {s.paddle_api_key.get_secret_value()}", "Content-Type": "application/json"},
    )


def _call(method: str, path: str, **kw: Any) -> dict[str, Any]:
    with _client() as c:
        try:
            r = c.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise ApiError(
                "The payment provider is unavailable. Try again shortly.", code="billing_unavailable", status_code=503
            ) from e
    if r.status_code >= 400:
        raise ApiError("The payment provider rejected the request.", code="billing_provider_error", status_code=502)
    return r.json().get("data", {})


def create_checkout(user: User, sub: Subscription, plan: str) -> dict[str, Any]:
    s = get_settings()
    price = plan_to_price(plan)
    if not s.paddle_enabled or not price:
        raise ApiError("Billing is not configured on this deployment.", code="billing_not_configured", status_code=503)
    body: dict[str, Any] = {
        "items": [{"price_id": price, "quantity": 1}],
        "custom_data": {"dmx_user_id": str(user.id), "dmx_plan": plan},
        "collection_mode": "automatic",
    }
    if sub.paddle_customer_id:
        body["customer_id"] = sub.paddle_customer_id
    data = _call("POST", "/transactions", json=body)
    return {
        "provider": "paddle",
        "environment": s.paddle_environment,
        "client_token": s.paddle_client_token,
        "transaction_id": data.get("id"),
        "checkout_url": (data.get("checkout") or {}).get("url"),
        "customer_email": user.email,
    }


def portal_url(sub: Subscription) -> str:
    if not sub.paddle_customer_id:
        raise ApiError(
            "No billing account yet — subscribe to a paid plan first.", code="no_billing_account", status_code=409
        )
    body = {"subscription_ids": [sub.paddle_subscription_id]} if sub.paddle_subscription_id else {}
    data = _call("POST", f"/customers/{sub.paddle_customer_id}/portal-sessions", json=body)
    return str(((data.get("urls") or {}).get("general") or {}).get("overview") or "")


def cancel(sub: Subscription) -> None:
    if not sub.paddle_subscription_id:
        raise ApiError("There is no paid subscription to cancel.", code="no_subscription", status_code=409)
    _call("POST", f"/subscriptions/{sub.paddle_subscription_id}/cancel", json={"effective_from": "next_billing_period"})


def change_plan(sub: Subscription, plan: str) -> None:
    price = plan_to_price(plan)
    if not sub.paddle_subscription_id or not price:
        raise ApiError("Plan changes need an active paid subscription.", code="no_subscription", status_code=409)
    _call(
        "PATCH",
        f"/subscriptions/{sub.paddle_subscription_id}",
        json={"items": [{"price_id": price, "quantity": 1}], "proration_billing_mode": "prorated_immediately"},
    )


def fetch_subscription(paddle_subscription_id: str) -> dict[str, Any]:
    return _call("GET", f"/subscriptions/{paddle_subscription_id}")


# --- webhook processing -----------------------------------------------------------------------


def _parse_ts(v: str | None) -> dt.datetime | None:
    if not v:
        return None
    try:
        return dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None


def _find_subscription(db: Session, data: dict[str, Any]) -> Subscription | None:
    custom = data.get("custom_data") or {}
    uid = custom.get("dmx_user_id")
    if uid:
        try:
            sub = db.scalar(select(Subscription).where(Subscription.user_id == uuid.UUID(str(uid))))
            if sub:
                return sub
        except ValueError:
            pass
    sid = data.get("subscription_id") or (data.get("id") if str(data.get("id", "")).startswith("sub_") else None)
    if sid:
        sub = db.scalar(select(Subscription).where(Subscription.paddle_subscription_id == sid))
        if sub:
            return sub
    cid = data.get("customer_id")
    if cid:
        return db.scalar(select(Subscription).where(Subscription.paddle_customer_id == cid))
    return None


STATUS_MAP = {
    "active": "active",
    "trialing": "trialing",
    "past_due": "past_due",
    "paused": "paused",
    "canceled": "canceled",
}


def apply_subscription(db: Session, sub: Subscription, data: dict[str, Any], occurred: dt.datetime | None) -> None:
    if occurred and sub.last_event_at and occurred < sub.last_event_at:
        return  # out-of-order older event: ignore for state
    old_plan, old_status = sub.plan, sub.status
    items = data.get("items") or []
    price_id = ((items[0].get("price") or {}).get("id") if items else None) or sub.paddle_price_id
    plan = price_to_plan().get(price_id or "", sub.plan)
    status = STATUS_MAP.get(str(data.get("status")), sub.status)
    sub.paddle_subscription_id = data.get("id") or sub.paddle_subscription_id
    sub.paddle_customer_id = data.get("customer_id") or sub.paddle_customer_id
    sub.paddle_price_id = price_id
    period = data.get("current_billing_period") or {}
    sub.period_start = _parse_ts(period.get("starts_at")) or sub.period_start
    sub.period_end = _parse_ts(period.get("ends_at")) or sub.period_end
    sched = data.get("scheduled_change") or {}
    sub.cancel_at_period_end = sched.get("action") == "cancel"
    if status == "past_due" and old_status != "past_due":
        sub.grace_until = utcnow() + dt.timedelta(days=get_settings().billing_grace_days)
    elif status in ("active", "trialing"):
        sub.grace_until = None
    sub.plan = plan if status != "canceled" else sub.plan
    sub.status = status
    sub.last_event_at = occurred or utcnow()
    if (old_plan, old_status) != (sub.plan, sub.status):
        audit(
            db,
            "billing.subscription_changed",
            actor_type="system",
            tenant_user_id=sub.user_id,
            resource_type="subscription",
            resource_id=sub.id,
            metadata={"from": f"{old_plan}/{old_status}", "to": f"{sub.plan}/{sub.status}"},
        )
        if status == "canceled":
            notify(
                db,
                sub.user_id,
                "subscription_canceled",
                "Your subscription was canceled",
                "Your databases keep running on the Free plan limits. Databases above those limits will be "
                "stopped (never deleted) after the grace period described in our billing policy.",
                severity="warning",
                link="/billing",
                dedupe_key=f"sub-canceled:{sub.paddle_subscription_id}",
            )
        elif sub.plan != old_plan:
            notify(
                db,
                sub.user_id,
                "plan_changed",
                f"You're now on the {sub.plan.title()} plan",
                "Your new limits are active.",
                link="/billing",
                dedupe_key=f"plan:{sub.plan}:{occurred}",
            )


def process_event(db: Session, event: dict[str, Any]) -> str:
    event_id = str(event.get("event_id") or event.get("notification_id") or "")
    etype = str(event.get("event_type") or "")
    if not event_id or not etype:
        raise ValueError("malformed event")
    data = event.get("data") or {}
    occurred = _parse_ts(event.get("occurred_at"))
    rec = BillingEvent(
        id=uuid.uuid4(),
        provider="paddle",
        event_id=event_id,
        event_type=etype,
        occurred_at=occurred,
        payload={
            "data_id": data.get("id"),
            "status": data.get("status"),
            "customer_id": data.get("customer_id"),
            "subscription_id": data.get("subscription_id"),
        },
    )
    try:
        with db.begin_nested():
            db.add(rec)
            db.flush()
    except IntegrityError:
        return "duplicate"
    sub = _find_subscription(db, data)
    rec.user_id = sub.user_id if sub else None
    if sub is None:
        rec.status = "unmatched"
        rec.processed_at = utcnow()
        return "unmatched"
    if etype.startswith("subscription."):
        apply_subscription(db, sub, data, occurred)
    elif etype in ("transaction.completed", "transaction.paid"):
        _record_payment(db, sub, data, "completed")
        if data.get("customer_id") and not sub.paddle_customer_id:
            sub.paddle_customer_id = data["customer_id"]
    elif etype == "transaction.payment_failed":
        _record_payment(db, sub, data, "failed")
        notify(
            db,
            sub.user_id,
            "payment_failed",
            "Payment failed",
            f"We couldn't process your payment. Your plan stays active for {get_settings().billing_grace_days} days "
            "while you update your payment method.",
            severity="error",
            link="/billing",
            dedupe_key=f"payfail:{data.get('id')}",
        )
    elif etype.startswith("adjustment.") and data.get("action") == "refund" and data.get("status") == "approved":
        pay = db.scalar(select(Payment).where(Payment.provider_transaction_id == data.get("transaction_id")))
        if pay:
            pay.status = "refunded"
    rec.status = "processed"
    rec.processed_at = utcnow()
    return "processed"


def _record_payment(db: Session, sub: Subscription, data: dict[str, Any], status: str) -> None:
    tid = data.get("id")
    if not tid:
        return
    totals = (data.get("details") or {}).get("totals") or {}
    try:
        amount = int(totals.get("grand_total") or totals.get("total") or 0)
    except (TypeError, ValueError):
        amount = 0
    pay = db.scalar(select(Payment).where(Payment.provider_transaction_id == tid))
    if pay is None:
        pay = Payment(
            id=uuid.uuid4(),
            user_id=sub.user_id,
            provider_transaction_id=tid,
            status=status,
            amount_cents=amount,
            currency=str(data.get("currency_code") or "USD")[:3],
        )
        db.add(pay)
    pay.status = status
    pay.amount_cents = amount or pay.amount_cents
    pay.invoice_number = data.get("invoice_number") or pay.invoice_number
    pay.billed_at = _parse_ts(data.get("billed_at")) or pay.billed_at or utcnow()
    pay.description = "DatabaseMax subscription"


def reconcile_subscriptions(db: Session) -> int:
    """Pull authoritative subscription state from Paddle (catches missed webhooks)."""
    if not get_settings().paddle_enabled:
        return 0
    n = 0
    for sub in db.scalars(select(Subscription).where(Subscription.paddle_subscription_id.is_not(None))):
        try:
            data = fetch_subscription(sub.paddle_subscription_id)  # type: ignore[arg-type]
        except ApiError:
            continue
        apply_subscription(db, sub, data, None)
        n += 1
    return n

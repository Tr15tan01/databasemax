from __future__ import annotations

import datetime as dt
import json
import logging
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.billing import paddle
from app.core.config import get_settings
from app.core.db import get_db, set_rls
from app.core.errors import ApiError
from app.core.logging import log_event
from app.models import Payment, Plan
from app.schemas.common import ApiModel, Message, OutModel
from app.security.deps import Principal, require_scope, require_session_user, require_verified
from app.services import plans
from app.services.audit import audit

router = APIRouter(prefix="/billing", tags=["billing"])
log = logging.getLogger("dmx.billing")


class SubscriptionOut(OutModel):
    plan: str
    plan_name: str
    effective_plan: str
    status: str
    price_cents: int
    currency: str
    period_start: dt.datetime | None
    period_end: dt.datetime | None
    cancel_at_period_end: bool
    grace_until: dt.datetime | None
    has_billing_account: bool
    billing_enabled: bool


class PlanChangeIn(ApiModel):
    plan: Literal["developer", "pro"]


class PaymentOut(OutModel):
    id: uuid.UUID
    provider_transaction_id: str
    status: str
    amount_cents: int
    currency: str
    invoice_number: str | None
    description: str | None
    billed_at: dt.datetime | None


@router.get("/plans", summary="Plans available for purchase")
def billing_plans(db: Session = Depends(get_db, scope="function")) -> list[dict[str, object]]:
    rows = db.scalars(select(Plan).where(Plan.active.is_(True)).order_by(Plan.sort_order))
    return [
        {
            "id": p.id,
            "name": p.name,
            "price_cents": p.price_cents,
            "currency": p.currency,
            "features": p.features,
            "limits": p.entitlements,
            "purchasable": p.id == "free" or bool(paddle.plan_to_price(p.id)),
        }
        for p in rows
    ]


@router.get("/subscription", response_model=SubscriptionOut, summary="Your subscription")
def get_subscription(
    db: Session = Depends(get_db, scope="function"), principal: Principal = Depends(require_scope("usage:read"))
) -> SubscriptionOut:
    sub = plans.get_subscription(db, principal.user.id)
    ent = plans.effective_entitlements(db, sub)
    plan = db.get(Plan, sub.plan)
    return SubscriptionOut(
        plan=sub.plan,
        plan_name=plan.name if plan else sub.plan,
        effective_plan=ent.plan_id,
        status=sub.status,
        price_cents=plan.price_cents if plan else 0,
        currency="USD",
        period_start=sub.period_start,
        period_end=sub.period_end,
        cancel_at_period_end=sub.cancel_at_period_end,
        grace_until=sub.grace_until,
        has_billing_account=bool(sub.paddle_customer_id),
        billing_enabled=get_settings().paddle_enabled,
    )


@router.post("/checkout", summary="Start a Paddle checkout for a plan")
def checkout(
    body: PlanChangeIn,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_session_user),
) -> dict[str, object]:
    require_verified(principal)
    sub = plans.get_subscription(db, principal.user.id)
    if sub.paddle_subscription_id and sub.status in ("active", "trialing", "past_due"):
        raise ApiError(
            "You already have a subscription — change plans instead.", code="already_subscribed", status_code=409
        )
    out = paddle.create_checkout(principal.user, sub, body.plan)
    audit(db, "billing.checkout_started", principal=principal, metadata={"plan": body.plan})
    return out


@router.post("/change-plan", response_model=Message, summary="Upgrade or downgrade between paid plans")
def change_plan(
    body: PlanChangeIn,
    db: Session = Depends(get_db, scope="function"),
    principal: Principal = Depends(require_session_user),
) -> Message:
    sub = plans.get_subscription(db, principal.user.id)
    paddle.change_plan(sub, body.plan)
    audit(db, "billing.plan_change_requested", principal=principal, metadata={"plan": body.plan})
    return Message(status="requested", message="Your plan will update as soon as Paddle confirms the change.")


@router.post("/cancel", response_model=Message, summary="Cancel at the end of the billing period")
def cancel(
    db: Session = Depends(get_db, scope="function"), principal: Principal = Depends(require_session_user)
) -> Message:
    sub = plans.get_subscription(db, principal.user.id)
    paddle.cancel(sub)
    audit(db, "billing.cancel_requested", principal=principal)
    return Message(status="requested", message="Your subscription will end at the close of the billing period.")


@router.post("/portal", summary="Open the Paddle customer portal")
def portal(
    db: Session = Depends(get_db, scope="function"), principal: Principal = Depends(require_session_user)
) -> dict[str, str]:
    sub = plans.get_subscription(db, principal.user.id)
    return {"url": paddle.portal_url(sub)}


@router.get("/payments", response_model=list[PaymentOut], summary="Payment history")
def payments(
    db: Session = Depends(get_db, scope="function"), principal: Principal = Depends(require_scope("usage:read"))
) -> list[PaymentOut]:
    rows = db.scalars(
        select(Payment).where(Payment.user_id == principal.user.id).order_by(Payment.created_at.desc()).limit(100)
    )
    return [PaymentOut.model_validate(p) for p in rows]


class WebhookAck(BaseModel):
    status: str


@router.post("/webhooks/paddle", response_model=WebhookAck, summary="Paddle webhook receiver", include_in_schema=True)
async def paddle_webhook(request: Request, db: Session = Depends(get_db, scope="function")) -> WebhookAck:
    raw = await request.body()
    try:
        paddle.verify_signature(raw, request.headers.get("paddle-signature"))
    except paddle.SignatureInvalid as e:
        log_event(log, "webhook rejected", reason=str(e))
        raise ApiError("Invalid signature.", code="invalid_signature", status_code=401) from e
    try:
        event = json.loads(raw)
    except ValueError as e:
        raise ApiError("Malformed payload.", code="bad_request") from e
    set_rls(db, system=True)
    result = paddle.process_event(db, event)
    log_event(log, "webhook processed", event_type=event.get("event_type"), result=result)
    return WebhookAck(status=result)

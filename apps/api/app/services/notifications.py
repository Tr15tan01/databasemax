"""In-app notifications with optional email delivery.

Deduplication: callers pass a `dedupe_key` (e.g. "db-ready:<db id>") so retried jobs don't
create duplicate notifications or emails.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Notification, User
from app.services.email import enqueue_email

# type -> (essential, default_email)
NOTIFICATION_TYPES: dict[str, tuple[bool, bool]] = {
    "account_verified": (False, False),
    "database_ready": (False, True),
    "database_failed": (True, True),
    "database_stopped": (False, False),
    "database_started": (False, False),
    "database_deleted": (False, False),
    "credentials_rotated": (True, True),
    "backup_completed": (False, False),
    "backup_failed": (True, True),
    "usage_threshold": (False, True),
    "plan_changed": (False, True),
    "payment_failed": (True, True),
    "subscription_canceled": (True, True),
    "security_login": (True, True),
    "admin_action": (True, True),
    "approval_requested": (True, True),
}


def email_enabled_for(user: User, ntype: str) -> bool:
    essential, default = NOTIFICATION_TYPES.get(ntype, (False, False))
    if essential:
        return True
    prefs = (user.preferences or {}).get("email_notifications", {})
    return bool(prefs.get(ntype, default))


def notify(
    db: Session,
    user_id: uuid.UUID,
    ntype: str,
    title: str,
    body: str = "",
    *,
    severity: str = "info",
    link: str | None = None,
    dedupe_key: str | None = None,
    email: bool = True,
) -> Notification | None:
    if dedupe_key and db.scalar(
        select(Notification.id).where(Notification.user_id == user_id, Notification.dedupe_key == dedupe_key)
    ):
        return None
    n = Notification(
        id=uuid.uuid4(),
        user_id=user_id,
        type=ntype,
        severity=severity,
        title=title[:200],
        body=body,
        link=link,
        dedupe_key=dedupe_key,
    )
    try:
        with db.begin_nested():
            db.add(n)
            db.flush()
    except IntegrityError:
        return None
    if email:
        user = db.get(User, user_id)
        if user and user.status == "active" and email_enabled_for(user, ntype):
            url = get_settings().public_web_url.rstrip("/") + link if link else None
            enqueue_email(
                db,
                to=user.email,
                template="notification",
                user_id=user_id,
                context={"title": title, "body": body, "url": url},
                dedupe_key=f"notif:{user_id}:{dedupe_key}" if dedupe_key else None,
            )
    return n

"""Security event monitoring: signals derived from the audit log and login attempts.

Thresholds are deliberately simple and documented in docs/security/incident-response.md; they
surface in Admin → Security and Admin → Infrastructure alerts. Nothing here stores or returns
passwords, tokens or full IP lists to the browser beyond what admins already see in the audit log.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AuditEvent, LoginAttempt, utcnow

# (window, threshold) pairs
FAILED_LOGINS_PER_HOUR_WARN = 100
FAILED_LOGINS_PER_ACCOUNT_HOUR = 20
FAILED_LOGINS_PER_IP_HOUR = 50
REVEALS_PER_USER_DAY = 50
SECURITY_ACTIONS = (
    "admin.role_granted",
    "admin.role_revoked",
    "admin.user_suspend",
    "admin.user_reactivate",
    "admin.database_suspend",
    "admin.database_unsuspend",
    "auth.password_changed",
    "auth.password_reset",
    "auth.sessions_revoked",
    "auth.oauth_linked",
    "api_key.create",
    "api_key.revoke",
    "database.credentials_revealed",
    "database.credentials_rotated",
    "backup.verification_failed",
    "infrastructure.orphan_removed",
    "infrastructure.instance_missing",
)


def summary(db: Session) -> dict[str, Any]:
    now = utcnow()
    hour, day = now - dt.timedelta(hours=1), now - dt.timedelta(hours=24)

    def failed_since(t: dt.datetime) -> int:
        return (
            db.scalar(
                select(func.count(LoginAttempt.id)).where(LoginAttempt.success.is_(False), LoginAttempt.created_at >= t)
            )
            or 0
        )

    top_accounts = db.execute(
        select(LoginAttempt.email_hash, func.count(LoginAttempt.id).label("n"))
        .where(LoginAttempt.success.is_(False), LoginAttempt.created_at >= hour)
        .group_by(LoginAttempt.email_hash)
        .order_by(func.count(LoginAttempt.id).desc())
        .limit(5)
    ).all()
    top_ips = db.execute(
        select(LoginAttempt.ip, func.count(LoginAttempt.id).label("n"))
        .where(LoginAttempt.success.is_(False), LoginAttempt.created_at >= hour, LoginAttempt.ip.is_not(None))
        .group_by(LoginAttempt.ip)
        .order_by(func.count(LoginAttempt.id).desc())
        .limit(5)
    ).all()
    reveals = db.execute(
        select(AuditEvent.actor_user_id, func.count(AuditEvent.id))
        .where(AuditEvent.action == "database.credentials_revealed", AuditEvent.created_at >= day)
        .group_by(AuditEvent.actor_user_id)
        .order_by(func.count(AuditEvent.id).desc())
        .limit(5)
    ).all()
    by_action = dict(
        db.execute(
            select(AuditEvent.action, func.count(AuditEvent.id))
            .where(AuditEvent.action.in_(SECURITY_ACTIONS), AuditEvent.created_at >= day)
            .group_by(AuditEvent.action)
        ).all()
    )
    admin_actions = (
        db.scalar(
            select(func.count(AuditEvent.id)).where(AuditEvent.actor_type == "admin", AuditEvent.created_at >= day)
        )
        or 0
    )
    failures = (
        db.scalar(
            select(func.count(AuditEvent.id)).where(AuditEvent.outcome == "failure", AuditEvent.created_at >= day)
        )
        or 0
    )

    failed_1h = failed_since(hour)
    alerts: list[dict[str, str]] = []
    if failed_1h >= FAILED_LOGINS_PER_HOUR_WARN:
        alerts.append(
            {
                "severity": "warning",
                "message": f"{failed_1h} failed sign-ins in the last hour (possible credential stuffing)",
            }
        )
    for _h, n in top_accounts:
        if n >= FAILED_LOGINS_PER_ACCOUNT_HOUR:
            alerts.append({"severity": "warning", "message": f"One account had {n} failed sign-ins in the last hour"})
            break
    for ip, n in top_ips:
        if n >= FAILED_LOGINS_PER_IP_HOUR:
            alerts.append({"severity": "warning", "message": f"{n} failed sign-ins from {ip} in the last hour"})
    for _uid, n in reveals:
        if n >= REVEALS_PER_USER_DAY:
            alerts.append({"severity": "warning", "message": f"A user revealed database passwords {n} times in 24 h"})
            break
    if by_action.get("admin.role_granted"):
        alerts.append({"severity": "info", "message": "An admin role was granted in the last 24 hours"})
    if by_action.get("backup.verification_failed"):
        alerts.append({"severity": "critical", "message": "A backup restore verification failed in the last 24 hours"})

    return {
        "failed_logins": {"last_hour": failed_1h, "last_24h": failed_since(day)},
        "top_failed_ips_1h": [{"ip": str(ip), "count": n} for ip, n in top_ips],
        "accounts_with_failures_1h": len(top_accounts),
        "max_failures_single_account_1h": top_accounts[0][1] if top_accounts else 0,
        "credential_reveals_24h": sum(n for _u, n in reveals),
        "security_actions_24h": by_action,
        "admin_actions_24h": admin_actions,
        "failed_operations_24h": failures,
        "thresholds": {
            "failed_logins_per_hour": FAILED_LOGINS_PER_HOUR_WARN,
            "failed_logins_per_account_hour": FAILED_LOGINS_PER_ACCOUNT_HOUR,
            "failed_logins_per_ip_hour": FAILED_LOGINS_PER_IP_HOUR,
            "reveals_per_user_day": REVEALS_PER_USER_DAY,
        },
        "alerts": alerts,
        "generated_at": now,
    }


def recent_events(db: Session, limit: int = 50) -> list[AuditEvent]:
    return list(
        db.scalars(
            select(AuditEvent)
            .where((AuditEvent.action.in_(SECURITY_ACTIONS)) | (AuditEvent.outcome == "failure"))
            .order_by(AuditEvent.created_at.desc())
            .limit(limit)
        )
    )

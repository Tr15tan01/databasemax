"""Transactional email via an outbox.

`enqueue_email` writes an outbox row in the caller's transaction (so an email is sent iff the
business change commits). Bodies can contain one-time links, so they're stored encrypted and
destroyed after delivery. The worker delivers with retries and dedupe.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import logging
import smtplib
import ssl
import uuid
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import EmailOutbox, utcnow
from app.security.secrets_store import secret_store

log = logging.getLogger("dmx.email")

# In-memory sink for tests (DMX_EMAIL_BACKEND=memory)
SENT: list[dict[str, str]] = []


def _layout(title: str, paragraphs: list[str], action: tuple[str, str] | None = None) -> tuple[str, str]:
    text_parts = [title, "", *paragraphs]
    body_html = "".join(f"<p style='margin:0 0 14px;color:#334155'>{html.escape(p)}</p>" for p in paragraphs)
    if action:
        label, url = action
        text_parts += ["", f"{label}: {url}"]
        body_html += (
            f"<p style='margin:22px 0'><a href='{html.escape(url, quote=True)}' "
            "style='background:#0f172a;color:#fff;padding:10px 16px;border-radius:8px;text-decoration:none;"
            f"font-weight:600'>{html.escape(label)}</a></p>"
            f"<p style='font-size:12px;color:#64748b'>Or paste this link into your browser:<br>{html.escape(url)}</p>"
        )
    text_parts += ["", "— DatabaseMax"]
    doc = (
        "<!doctype html><html><body style='font-family:ui-sans-serif,system-ui,sans-serif;background:#f8fafc;"
        "padding:24px'><div style='max-width:520px;margin:0 auto;background:#fff;border:1px solid #e2e8f0;"
        f"border-radius:12px;padding:28px'><h1 style='font-size:18px;margin:0 0 16px;color:#0f172a'>"
        f"{html.escape(title)}</h1>{body_html}<p style='font-size:12px;color:#94a3b8;margin-top:24px'>"
        "DatabaseMax · Managed PostgreSQL</p></div></body></html>"
    )
    return "\n".join(text_parts), doc


TEMPLATES: dict[str, Any] = {
    "verify_email": lambda ctx: (
        "Verify your email address",
        _layout(
            "Confirm your email",
            [
                "Welcome to DatabaseMax. Confirm your email address to create databases.",
                f"This link expires in {ctx['hours']} hours.",
            ],
            ("Verify email", ctx["url"]),
        ),
    ),
    "reset_password": lambda ctx: (
        "Reset your password",
        _layout(
            "Reset your password",
            [
                "We received a request to reset your DatabaseMax password.",
                f"The link expires in {ctx['minutes']} minutes and can be used once. "
                "If you didn't ask for this, you can ignore this email.",
            ],
            ("Choose a new password", ctx["url"]),
        ),
    ),
    "password_changed": lambda ctx: (
        "Your password was changed",
        _layout(
            "Your password was changed",
            [
                "The password for your DatabaseMax account was just changed and other sessions were signed out.",
                "If this wasn't you, reset your password immediately and contact support.",
            ],
        ),
    ),
    "new_sign_in": lambda ctx: (
        "New sign-in to your account",
        _layout(
            "New sign-in detected",
            [
                f"Your account was signed in from a new network ({ctx.get('ip') or 'unknown IP'}).",
                "If this was you, no action is needed. Otherwise, change your password and revoke sessions "
                "from Settings → Security.",
            ],
        ),
    ),
    "notification": lambda ctx: (
        ctx["title"],
        _layout(ctx["title"], [ctx["body"]], ("Open DatabaseMax", ctx["url"]) if ctx.get("url") else None),
    ),
}


def enqueue_email(
    db: Session,
    *,
    to: str,
    template: str,
    context: dict[str, Any],
    user_id: uuid.UUID | None = None,
    dedupe_key: str | None = None,
) -> EmailOutbox | None:
    subject, (text_body, html_body) = TEMPLATES[template](context)
    if dedupe_key and db.scalar(select(EmailOutbox.id).where(EmailOutbox.dedupe_key == dedupe_key)):
        return None
    secret_id = secret_store.put(db, json.dumps({"text": text_body, "html": html_body}), purpose="email")
    row = EmailOutbox(
        id=uuid.uuid4(),
        user_id=user_id,
        to_address=to,
        template=template,
        subject=subject,
        body_secret_id=secret_id,
        dedupe_key=dedupe_key,
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        return None
    return row


def _deliver(to: str, subject: str, text_body: str, html_body: str, outbox_id: uuid.UUID) -> None:
    s = get_settings()
    msg = EmailMessage()
    msg["From"] = s.email_from
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = f"<{outbox_id}@databasemax>"
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    if s.email_backend == "memory":
        SENT.append({"to": to, "subject": subject, "text": text_body})
        return
    if s.email_backend == "file":
        d = Path(s.email_file_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{utcnow():%Y%m%dT%H%M%S}-{outbox_id}.eml").write_bytes(bytes(msg))
        return
    if not s.smtp_host:
        raise RuntimeError("SMTP_HOST not configured")
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
        if s.smtp_starttls:
            smtp.starttls(context=ssl.create_default_context())
        if s.smtp_username and s.smtp_password:
            smtp.login(s.smtp_username, s.smtp_password.get_secret_value())
        smtp.send_message(msg)


def deliver_pending(db: Session, limit: int = 20) -> int:
    """Called by the worker with a system session. Returns number delivered."""
    rows = db.scalars(
        select(EmailOutbox)
        .where(EmailOutbox.status == "pending", EmailOutbox.next_attempt_at <= utcnow())
        .order_by(EmailOutbox.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    sent = 0
    for row in rows:
        try:
            body = (
                json.loads(secret_store.get(db, row.body_secret_id)) if row.body_secret_id else {"text": "", "html": ""}
            )
            _deliver(row.to_address, row.subject, body["text"], body["html"], row.id)
            row.status = "sent"
            row.sent_at = utcnow()
            if row.body_secret_id:
                secret_store.destroy(db, row.body_secret_id)
            sent += 1
        except Exception as e:
            log.warning("email delivery failed: %s (attempt %s)", type(e).__name__, row.attempts + 1)
            row.attempts += 1
            row.last_error = type(e).__name__[:300]
            if row.attempts >= 8:
                row.status = "failed"
            else:
                row.next_attempt_at = utcnow() + dt.timedelta(seconds=min(30 * 2**row.attempts, 3600))
    return sent

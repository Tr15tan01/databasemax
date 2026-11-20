"""Operator-created accounts without the email round-trip (CLI) and admin 'mark verified'."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.cli import create_user, verify_user
from app.core.db import system_session
from app.models import AuditEvent, EmailOutbox, User
from app.services import email
from tests.test_admin import _make_admin

PW = "a-Very-good-passphrase-42"


def _addr() -> str:
    return f"cli-{uuid.uuid4().hex[:10]}@example.com"


def test_create_user_is_verified_and_can_sign_in_and_create_databases(app, monkeypatch):
    from tests.conftest import ApiClient

    addr = _addr()
    monkeypatch.setenv("DMX_USER_PASSWORD", PW)
    assert create_user(addr.upper(), "Tester", unverified=False) == 0
    with system_session() as db:
        u = db.scalar(select(User).where(User.email == addr))
        assert u is not None and u.email_verified_at is not None
        # No verification email was queued, and the audit says who created it.
        assert db.scalar(select(EmailOutbox.id).where(EmailOutbox.to_address == addr)) is None
        ev = db.scalar(
            select(AuditEvent).where(AuditEvent.action == "auth.register", AuditEvent.tenant_user_id == u.id)
        )
        assert ev is not None and ev.metadata_["source"] == "cli" and ev.actor_type == "system"
    c = ApiClient(app)
    assert c.post("/auth/login", json={"email": addr, "password": PW}).status_code == 200
    p = c.post("/projects", json={"name": "P"}).json()
    assert c.post("/databases", json={"project_id": p["id"], "name": "db"}).status_code == 202


def test_create_user_applies_password_policy_and_rejects_duplicates(monkeypatch):
    addr = _addr()
    monkeypatch.setenv("DMX_USER_PASSWORD", "short")
    assert create_user(addr, None, unverified=False) == 1
    monkeypatch.setenv("DMX_USER_PASSWORD", PW)
    assert create_user("not-an-email", None, unverified=False) == 1
    assert create_user(addr, None, unverified=False) == 0
    assert create_user(addr, None, unverified=False) == 1


def test_unverified_flag_sends_normal_verification(monkeypatch):
    addr = _addr()
    monkeypatch.setenv("DMX_USER_PASSWORD", PW)
    email.SENT.clear()
    assert create_user(addr, None, unverified=True) == 0
    with system_session() as db:
        assert db.scalar(select(User.email_verified_at).where(User.email == addr)) is None
        assert db.scalar(select(EmailOutbox.id).where(EmailOutbox.to_address == addr)) is not None


def test_verify_user_cli(make_user):
    u = make_user(verified=False)
    assert u.get("/auth/me").json()["email_verified"] is False
    assert verify_user(u.user["email"], "support request") == 0
    assert u.get("/auth/me").json()["email_verified"] is True
    assert verify_user(u.user["email"], "again") == 0
    assert verify_user("nobody@example.com", "x") == 1


def test_admin_can_mark_email_verified(make_user):
    admin = _make_admin(make_user)
    target = make_user(verified=False)
    r = admin.post(f"/admin/users/{target.user['id']}/verify-email", json={"reason": "QA account"})
    assert r.status_code == 200 and r.json()["status"] == "verified"
    assert target.get("/auth/me").json()["email_verified"] is True
    with system_session() as db:
        ev = db.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "admin.user_email_verified", AuditEvent.resource_id == target.user["id"]
            )
        )
        assert ev is not None and ev.reason == "QA account" and ev.actor_type == "admin"
    # Ordinary users can't reach it.
    assert target.post(f"/admin/users/{target.user['id']}/verify-email", json={"reason": "x"}).status_code == 404

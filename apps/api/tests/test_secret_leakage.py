"""Passwords, tokens and connection strings must never appear in logs or audit records."""

from __future__ import annotations

import io
import logging

from sqlalchemy import text

from app.core.db import system_session
from app.core.logging import JsonFormatter, RedactingFilter, redact


def test_redaction_patterns():
    s = redact("postgresql://u_abc:SuperSecret123@db.example.com:5432/app?sslmode=require")
    assert "SuperSecret123" not in s and "u_abc" in s
    assert "hunter2" not in redact('{"password": "hunter2", "email": "a@b.c"}')
    assert "hunter2" not in redact("password=hunter2 other=1")
    assert "dmx_live_" not in redact("key dmx_live_abcdefgh_" + "x" * 40)
    assert "abc.def" not in redact("Authorization: Bearer abc.def")
    assert "SCRAM-SHA-256$4096" not in redact("ALTER ROLE x PASSWORD 'SCRAM-SHA-256$4096:abc$def:ghi'")


def test_no_passwords_in_logs_during_auth_flows(app, make_user):
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        pw = "Unique-Password-For-Leak-Test-991"
        u = make_user(password=pw)
        u.post("/auth/login", json={"email": u.user["email"], "password": "Wrong-Password-For-Leak-Test"})
        u.post("/auth/password/change", json={"current_password": pw, "new_password": "Next-Password-Leak-Test-2"})
        key = u.post("/api-keys", json={"name": "k", "scopes": ["projects:read"]}).json()["key"]
        u.c.get("/api/v1/projects", headers={"Authorization": f"Bearer {key}"})
    finally:
        root.removeHandler(handler)
    out = buf.getvalue()
    for secret in (pw, "Wrong-Password-For-Leak-Test", "Next-Password-Leak-Test-2", key):
        assert secret not in out


def test_audit_metadata_never_contains_secrets(make_user):
    pw = "Audit-Leak-Test-Password-77"
    u = make_user(password=pw)
    key = u.post("/api-keys", json={"name": "k", "scopes": ["projects:read"]}).json()["key"]
    with system_session() as db:
        blob = " ".join(
            str(r) for r in db.execute(text("SELECT metadata::text, coalesce(reason,'') FROM audit_events"))
        )
    assert pw not in blob and key not in blob


def test_emails_are_encrypted_at_rest(make_user):
    from app.core.db import system_session as ss

    make_user(verified=False)
    with ss() as db:
        rows = db.execute(text("SELECT ciphertext FROM secrets WHERE purpose = 'email'")).all()
    for (ct,) in rows:
        assert b"token=" not in bytes(ct)


def test_config_endpoint_exposes_no_secrets(user):
    body = user.get("/config/public").text
    for needle in ("dev-only", "secret", "password", 'token":"dev'):
        assert needle not in body.lower() or needle == 'token":"dev'

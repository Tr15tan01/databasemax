from __future__ import annotations

import datetime as dt

from sqlalchemy import select, update

from app.cli import grant_admin
from app.core.db import system_session
from app.models import AuditEvent, Session


def _make_admin(make_user):
    a = make_user()
    assert grant_admin(a.user["email"], "test operator") == 0
    a.user = a.get("/auth/me").json()
    return a


ADMIN_GETS = [
    "/admin/overview",
    "/admin/users",
    "/admin/databases",
    "/admin/jobs",
    "/admin/infrastructure",
    "/admin/audit",
    "/admin/billing",
    "/admin/admins",
]


def test_admin_routes_reject_ordinary_users(user):
    for path in ADMIN_GETS:
        r = user.get(path)
        assert r.status_code == 404, path


def test_admin_routes_reject_api_keys_even_for_admins(make_user):
    admin = _make_admin(make_user)
    key = admin.post("/api-keys", json={"name": "k", "scopes": ["projects:read"]}).json()["key"]
    r = admin.c.get("/api/v1/admin/overview", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 403


def test_admin_cannot_be_self_granted_via_profile(user):
    r = user.patch("/account", json={"preferences": {"is_admin": True}})
    assert r.status_code == 422
    assert user.get("/auth/me").json()["is_admin"] is False


def test_admin_can_inspect(make_user, user, project):
    user.post("/databases", json={"project_id": project["id"], "name": "inspect-me"})
    admin = _make_admin(make_user)
    assert admin.user["is_admin"] is True
    ov = admin.get("/admin/overview").json()
    assert ov["users"]["total"] >= 2 and ov["databases"]["total"] >= 1
    users = admin.get("/admin/users", params={"q": user.user["email"]}).json()
    assert users["total"] == 1 and users["items"][0]["databases"] == 1
    detail = admin.get(f"/admin/users/{user.user['id']}").json()
    assert detail["databases"][0]["name"] == "inspect-me"
    dbs = admin.get("/admin/databases", params={"q": "inspect-me"}).json()
    assert dbs["total"] == 1
    d = admin.get(f"/admin/databases/{dbs['items'][0]['id']}").json()
    assert "password" not in str(d).lower()
    assert admin.get("/admin/jobs", params={"status": "active"}).json()["total"] >= 1


def test_suspend_requires_recent_auth_and_is_audited(make_user, user):
    admin = _make_admin(make_user)
    with system_session() as db:
        db.execute(
            update(Session)
            .where(Session.user_id == admin.user["id"])
            .values(
                reauthenticated_at=dt.datetime.now(dt.UTC) - dt.timedelta(hours=1),
                created_at=dt.datetime.now(dt.UTC) - dt.timedelta(hours=1),
            )
        )
    r = admin.post(f"/admin/users/{user.user['id']}/suspend", json={"reason": "abuse report #42"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "reauthentication_required"
    assert admin.post("/auth/reauthenticate", json={"password": admin.password}).status_code == 200
    r = admin.post(f"/admin/users/{user.user['id']}/suspend", json={"reason": "abuse report #42"})
    assert r.status_code == 200
    assert user.get("/auth/me").status_code == 401  # sessions revoked
    with system_session() as db:
        ev = db.scalar(select(AuditEvent).where(AuditEvent.action == "admin.user_suspend"))
    assert ev.actor_type == "admin" and str(ev.actor_user_id) == admin.user["id"]
    assert ev.reason == "abuse report #42" and ev.outcome == "success"
    r = admin.post(f"/admin/users/{user.user['id']}/reactivate", json={"reason": "resolved"})
    assert r.status_code == 200


def test_admin_actions_require_reason(make_user, user):
    admin = _make_admin(make_user)
    r = admin.post(f"/admin/users/{user.user['id']}/revoke-sessions", json={})
    assert r.status_code == 422


def test_security_monitoring_flags_failed_logins(make_user, monkeypatch):
    from app.monitoring import security as sec

    admin = _make_admin(make_user)
    victim = make_user()
    monkeypatch.setattr(sec, "FAILED_LOGINS_PER_ACCOUNT_HOUR", 3)
    for _ in range(3):
        admin.c.post(
            "/api/v1/auth/login",
            json={"email": victim.user["email"], "password": "wrong-password-xyz"},
            headers={"Origin": "http://localhost:3000", "X-CSRF-Token": admin.c.cookies.get("dmx_csrf") or ""},
        )
    r = admin.get("/admin/security")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["failed_logins"]["last_hour"] >= 3
    assert any("failed sign-ins" in a["message"] for a in body["alerts"])
    assert any(e["action"] == "admin.role_granted" for e in body["recent_events"])
    assert "wrong-password-xyz" not in r.text  # attempted passwords are never recorded
    assert victim.get("/admin/security").status_code == 404

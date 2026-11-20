"""Cross-tenant authorization tests: a user (or their API key) must never see or act on another
tenant's projects, databases, credentials, events, usage or audit log."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select, text

from app.core.db import new_session
from app.models import Database, Project


def _setup(user, project):
    r = user.post("/databases", json={"project_id": project["id"], "name": "secret db"})
    assert r.status_code == 202, r.text
    return r.json()


def test_other_user_cannot_access_project_or_database(user, project, make_user):
    db = _setup(user, project)
    other = make_user()
    pid, did = project["id"], db["id"]
    assert other.get(f"/projects/{pid}").status_code == 404
    assert other.get(f"/projects/{pid}/databases").status_code == 404
    assert other.patch(f"/projects/{pid}", json={"name": "hijack"}).status_code == 404
    assert other.post(f"/projects/{pid}/archive").status_code == 404
    assert other.delete(f"/projects/{pid}", params={"confirm": project["name"]}).status_code == 404
    for path in ("", "/status", "/connection", "/events", "/usage"):
        assert other.get(f"/databases/{did}{path}").status_code == 404, path
    assert other.get(f"/databases/{did}/connection", params={"reveal": "true"}).status_code == 404
    assert other.patch(f"/databases/{did}", json={"name": "x"}).status_code == 404
    assert other.post(f"/databases/{did}/stop").status_code == 404
    assert other.post(f"/databases/{did}/credentials/rotate").status_code == 404
    assert other.delete(f"/databases/{did}", params={"confirm": "secret db"}).status_code == 404
    # cannot create a database in someone else's project by supplying their project id
    r = other.post("/databases", json={"project_id": pid, "name": "sneaky"})
    assert r.status_code == 404
    assert other.get("/databases").json()["total"] == 0
    assert other.get("/projects").json()["total"] == 0
    acts = other.get("/activity").json()["items"]
    assert all(a["resource_id"] not in (pid, did) for a in acts)


def test_other_users_api_key_cannot_access(user, project, make_user):
    db = _setup(user, project)
    other = make_user()
    key = other.post(
        "/api-keys", json={"name": "k", "scopes": ["databases:read", "projects:read", "credentials:read"]}
    ).json()["key"]
    h = {"Authorization": f"Bearer {key}"}
    assert other.c.get(f"/api/v1/databases/{db['id']}", headers=h).status_code == 404
    assert other.c.get(f"/api/v1/projects/{project['id']}", headers=h).status_code == 404
    assert other.c.get("/api/v1/databases", headers=h).json()["total"] == 0


def test_row_level_security_blocks_unscoped_queries(user, project, make_user):
    _setup(user, project)
    other = make_user()
    owner_id = uuid.UUID(user.user["id"])
    other_id = uuid.UUID(other.user["id"])
    # A session scoped to another tenant sees nothing, even with no WHERE clause at all.
    s = new_session(user_id=other_id)
    try:
        assert s.scalar(select(func.count()).select_from(Project)) == 0
        assert s.scalar(select(func.count()).select_from(Database)) == 0
        assert s.scalar(text("SELECT count(*) FROM database_credentials")) == 0
        assert s.scalar(text("SELECT count(*) FROM audit_events WHERE tenant_user_id = :o"), {"o": owner_id}) == 0
        assert s.scalar(text("SELECT count(*) FROM provisioning_jobs")) == 0
    finally:
        s.close()
    # An unscoped (no tenant) session also sees nothing.
    s = new_session()
    try:
        assert s.scalar(select(func.count()).select_from(Project)) == 0
    finally:
        s.close()
    # The owner's scope sees their rows.
    s = new_session(user_id=owner_id)
    try:
        assert s.scalar(select(func.count()).select_from(Project)) == 1
        assert s.scalar(select(func.count()).select_from(Database)) == 1
    finally:
        s.close()


def test_rls_blocks_cross_tenant_writes(user, project, make_user):
    other = make_user()
    s = new_session(user_id=uuid.UUID(other.user["id"]))
    try:
        n = s.execute(text("UPDATE projects SET name = 'pwned'")).rowcount
        assert n == 0
        s.rollback()
    finally:
        s.close()
    assert user.get(f"/projects/{project['id']}").json()["name"] == project["name"]

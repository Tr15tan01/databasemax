from __future__ import annotations


def _key(user, scopes, project_ids=None):
    body = {"name": "ci", "scopes": scopes}
    if project_ids:
        body["project_ids"] = project_ids
    r = user.post("/api-keys", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_key_shown_once_and_hashed(user):
    k = _key(user, ["projects:read"])
    assert k["key"].startswith("dmx_live_")
    listed = user.get("/api-keys").json()
    assert "key" not in listed[0]
    from sqlalchemy import text

    from app.core.db import system_session

    with system_session() as db:
        stored = db.execute(text("SELECT key_hash FROM api_keys WHERE id = :i"), {"i": k["id"]}).scalar()
    assert k["key"].encode() not in bytes(stored)


def test_scopes_enforced(user, project):
    k = _key(user, ["projects:read"])
    h = {"Authorization": f"Bearer {k['key']}"}
    assert user.c.get("/api/v1/projects", headers=h).status_code == 200
    r = user.c.post("/api/v1/projects", json={"name": "nope"}, headers=h)
    assert r.status_code == 403 and r.json()["error"]["code"] == "insufficient_scope"
    r = user.c.get("/api/v1/databases", headers=h)
    assert r.status_code == 403


def test_bearer_requests_skip_csrf_but_need_valid_key(user):
    r = user.c.post("/api/v1/projects", json={"name": "x"}, headers={"Authorization": "Bearer dmx_live_bad"})
    assert r.status_code == 401


def test_revoked_key_stops_working(user):
    k = _key(user, ["projects:read"])
    h = {"Authorization": f"Bearer {k['key']}"}
    assert user.c.get("/api/v1/projects", headers=h).status_code == 200
    assert user.delete(f"/api-keys/{k['id']}").status_code == 200
    assert user.c.get("/api/v1/projects", headers=h).status_code == 401


def test_api_key_cannot_create_api_keys(user):
    k = _key(user, list(__import__("app.security.api_keys", fromlist=["SCOPES"]).SCOPES))
    r = user.c.post(
        "/api/v1/api-keys",
        json={"name": "x", "scopes": ["projects:read"]},
        headers={"Authorization": f"Bearer {k['key']}"},
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "session_required"


def test_project_restricted_key(user, project):
    other_project = user.post("/projects", json={"name": "Other"})
    # free plan allows only one project
    assert other_project.status_code == 402
    k = _key(user, ["projects:read", "databases:read", "databases:write"], [project["id"]])
    h = {"Authorization": f"Bearer {k['key']}"}
    assert user.c.get(f"/api/v1/projects/{project['id']}", headers=h).status_code == 200
    r = user.c.post("/api/v1/projects", json={"name": "y"}, headers=h)
    assert r.status_code == 403


def test_unknown_scope_rejected(user):
    r = user.post("/api-keys", json={"name": "x", "scopes": ["admin:*"]})
    assert r.status_code == 422


def test_api_key_actor_recorded_in_audit(user, project):
    k = _key(user, ["projects:write", "projects:read", "activity:read"])
    h = {"Authorization": f"Bearer {k['key']}"}
    r = user.c.patch(f"/api/v1/projects/{project['id']}", json={"description": "via api"}, headers=h)
    assert r.status_code == 200
    ev = user.get("/activity", params={"action": "project.update"}).json()["items"][0]
    assert ev["actor_type"] == "api_key" and ev["actor_api_key_id"] == k["id"]

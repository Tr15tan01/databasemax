from __future__ import annotations

import pytest

from app.models import Database
from app.provisioning import state_machine as sm
from app.provisioning.connection import ConnectionInfo, build_url


def test_rejects_infrastructure_parameters(user, project):
    for extra in (
        {"image": "evil/image"},
        {"host_path": "/"},
        {"privileged": True},
        {"network": "host"},
        {"env": {"A": "1"}},
        {"command": "sh"},
    ):
        r = user.post("/databases", json={"project_id": project["id"], "name": "db", **extra})
        assert r.status_code == 422, extra


def test_rejects_unsupported_version_and_profile(user, project):
    r = user.post("/databases", json={"project_id": project["id"], "name": "db", "postgres_version": "9.6"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "unsupported_version"
    r = user.post("/databases", json={"project_id": project["id"], "name": "db", "compute_profile": "huge"})
    assert r.status_code == 422
    r = user.post("/databases", json={"project_id": project["id"], "name": "db", "compute_profile": "medium"})
    assert r.status_code == 402 and r.json()["error"]["code"] == "quota_exceeded"


def test_rejects_bad_names(user, project):
    for name in ("", " ", "a" * 65, "<script>", "x;drop"):
        r = user.post("/databases", json={"project_id": project["id"], "name": name})
        assert r.status_code == 422, name
    for dbname in ("postgres", "Upper", "1abc", "a-b"):
        r = user.post("/databases", json={"project_id": project["id"], "name": "ok", "database_name": dbname})
        assert r.status_code == 422, dbname


def test_free_plan_database_quota(user, project):
    r = user.post("/databases", json={"project_id": project["id"], "name": "one"})
    assert r.status_code == 202
    assert r.json()["allocated_storage_mb"] == 500
    assert r.json()["max_connections"] == 20
    r = user.post("/databases", json={"project_id": project["id"], "name": "two"})
    assert r.status_code == 402 and r.json()["error"]["details"]["resource"] == "databases"


def test_storage_quota(user, project):
    r = user.post("/databases", json={"project_id": project["id"], "name": "big", "storage_mb": 600})
    assert r.status_code == 402


def test_idempotency(user, project):
    h = {"Idempotency-Key": "create-db-abcdef12"}
    r1 = user.post("/databases", json={"project_id": project["id"], "name": "idem"}, headers=h)
    r2 = user.post("/databases", json={"project_id": project["id"], "name": "idem"}, headers=h)
    assert r1.status_code == r2.status_code == 202
    assert r1.json()["id"] == r2.json()["id"]
    assert r2.headers.get("Idempotent-Replayed") == "true"
    r3 = user.post("/databases", json={"project_id": project["id"], "name": "different"}, headers=h)
    assert r3.status_code == 422 and r3.json()["error"]["code"] == "idempotency_key_reused"
    assert user.get("/databases").json()["total"] == 1


def test_duplicate_name_rejected_without_idempotency_key(make_user):
    from sqlalchemy import update

    from app.core.db import system_session
    from app.models import Subscription

    u = make_user()
    with system_session() as db:
        db.execute(update(Subscription).where(Subscription.user_id == u.user["id"]).values(plan="developer"))
    p = u.post("/projects", json={"name": "p"}).json()
    assert u.post("/databases", json={"project_id": p["id"], "name": "Same"}).status_code == 202
    r = u.post("/databases", json={"project_id": p["id"], "name": "same"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "name_taken"


def test_delete_requires_confirmation(user, project):
    d = user.post("/databases", json={"project_id": project["id"], "name": "to-delete"}).json()
    r = user.delete(f"/databases/{d['id']}", params={"confirm": "wrong"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "confirmation_mismatch"
    r = user.delete(f"/databases/{d['id']}", params={"confirm": "to-delete"})
    assert r.status_code == 202 and r.json()["status"] == "deleting"
    r = user.delete(f"/databases/{d['id']}", params={"confirm": "to-delete"})
    assert r.status_code == 409


def test_project_delete_requires_empty(user, project):
    user.post("/databases", json={"project_id": project["id"], "name": "keep"})
    r = user.delete(f"/projects/{project['id']}", params={"confirm": project["name"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "project_not_empty"


def test_connection_not_available_before_ready(user, project):
    d = user.post("/databases", json={"project_id": project["id"], "name": "pending"}).json()
    r = user.get(f"/databases/{d['id']}/connection")
    assert r.status_code == 409


def test_list_filters_and_search(user, project):
    user.post("/databases", json={"project_id": project["id"], "name": "Searchable"})
    assert user.get("/databases", params={"q": "search"}).json()["total"] == 1
    assert user.get("/databases", params={"q": "zzz"}).json()["total"] == 0
    assert user.get("/databases", params={"status": "provisioning"}).json()["total"] == 1
    assert user.get("/databases", params={"status": "running"}).json()["total"] == 0
    assert user.get("/databases", params={"sort": "name", "order": "asc"}).status_code == 200
    assert user.get("/databases", params={"sort": "evil"}).status_code == 422


def test_state_machine_rules():
    d = Database(status="pending")
    assert sm.can_transition("pending", "validating")
    assert not sm.can_transition("pending", "ready")
    assert not sm.can_transition("deleted", "ready")
    assert not sm.can_transition("ready", "provisioning")
    with pytest.raises(sm.InvalidTransition):
        sm.transition(None, d, "ready")  # type: ignore[arg-type]


@pytest.mark.parametrize("password", ["p@ss:w/rd?#[]", "üñî", "a b%20c", "plain"])
def test_connection_url_encoding(password):
    import urllib.parse

    info = ConnectionInfo(host="db-abc.db.example.com", port=5432, database="app_db", username="u_abc")
    url = build_url(info, password)
    parsed = urllib.parse.urlsplit(url)
    assert urllib.parse.unquote(parsed.password or "") == password
    assert parsed.hostname == "db-abc.db.example.com" and parsed.port == 5432
    assert parsed.path == "/app_db" and parsed.query == "sslmode=require"


def test_options_endpoint(user):
    r = user.get("/databases/options").json()
    assert r["postgres_versions"] == ["16"]
    assert [p["id"] for p in r["compute_profiles"] if p["available"]] == ["nano"]

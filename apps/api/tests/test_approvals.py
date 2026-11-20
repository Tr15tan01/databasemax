"""Phase 6: agent identities and human approval for destructive operations."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select, text

from app.core.db import system_session

ALL = [
    "projects:read",
    "projects:write",
    "projects:delete",
    "databases:read",
    "databases:write",
    "databases:delete",
    "activity:read",
]


def _key(user, *, kind="standard", requires_approval=False, scopes=ALL, name="bot"):
    r = user.post(
        "/api-keys",
        json={"name": name, "scopes": scopes, "kind": kind, "requires_approval": requires_approval},
    )
    assert r.status_code == 201, r.text
    k = r.json()
    return k, {"Authorization": f"Bearer {k['key']}"}


def _audit_actions(user_id: str) -> list[str]:
    with system_session() as db:
        return list(
            db.scalars(
                text("SELECT action FROM audit_events WHERE tenant_user_id = :u ORDER BY created_at"), {"u": user_id}
            )
        )


def test_agent_key_always_requires_approval(user):
    k, _h = _key(user, kind="agent", requires_approval=False)
    assert k["kind"] == "agent" and k["requires_approval"] is True
    r = user.patch(f"/api-keys/{k['id']}", json={"requires_approval": False})
    assert r.status_code == 400


def test_agent_project_delete_waits_for_approval_then_runs(user, project):
    _k, h = _key(user, kind="agent")
    r = user.c.delete(f"/api/v1/projects/{project['id']}", params={"confirm": project["name"]}, headers=h)
    assert r.status_code == 202, r.text
    assert r.headers["X-DMX-Approval"] == "pending"
    body = r.json()
    assert body["status"] == "pending_approval"
    appr = body["approval"]
    assert appr["action"] == "project.delete" and appr["status"] == "pending" and appr["api_key_name"] == "bot"

    # Nothing happened yet; a repeated request reuses the same pending approval.
    assert user.get(f"/projects/{project['id']}").status_code == 200
    r2 = user.c.delete(f"/api/v1/projects/{project['id']}", params={"confirm": project["name"]}, headers=h)
    assert r2.status_code == 202 and r2.json()["approval"]["id"] == appr["id"]

    # The owner is notified (essential: always emailed).
    notes = user.get("/notifications").json()["items"]
    assert any(n["type"] == "approval_requested" for n in notes)

    # The agent can poll its own request but can't approve it.
    got = user.c.get(f"/api/v1/approvals/{appr['id']}", headers=h)
    assert got.status_code == 200 and got.json()["status"] == "pending"
    denied = user.c.post(f"/api/v1/approvals/{appr['id']}/approve", json={}, headers=h)
    assert denied.status_code == 403
    assert user.get(f"/projects/{project['id']}").status_code == 200

    assert user.get("/approvals/count").json()["pending"] == 1
    ok = user.post(f"/approvals/{appr['id']}/approve", json={"reason": "cleanup"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "executed"
    assert user.get(f"/projects/{project['id']}").status_code == 404
    assert user.get("/approvals/count").json()["pending"] == 0
    # A decided request can't be decided again.
    assert user.post(f"/approvals/{appr['id']}/approve", json={}).status_code == 409

    actions = _audit_actions(user.user["id"])
    assert actions.index("approval.requested") < actions.index("approval.approved") < actions.index("project.delete")


def test_reject_changes_nothing(user, project):
    _k, h = _key(user, kind="agent")
    appr = user.c.delete(f"/api/v1/projects/{project['id']}", params={"confirm": project["name"]}, headers=h).json()[
        "approval"
    ]
    r = user.post(f"/approvals/{appr['id']}/reject", json={"reason": "not now"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert r.json()["decision_reason"] == "not now"
    assert user.get(f"/projects/{project['id']}").status_code == 200
    assert user.post(f"/approvals/{appr['id']}/approve", json={}).status_code == 409
    # A new request after a rejection creates a new approval.
    again = user.c.delete(f"/api/v1/projects/{project['id']}", params={"confirm": project["name"]}, headers=h)
    assert again.status_code == 202 and again.json()["approval"]["id"] != appr["id"]


def test_validation_happens_before_an_approval_is_created(user, project):
    _k, h = _key(user, kind="agent")
    r = user.c.delete(f"/api/v1/projects/{project['id']}", params={"confirm": "wrong"}, headers=h)
    assert r.status_code == 400
    assert user.get("/approvals").json()["total"] == 0


def test_standard_keys_unchanged_unless_opted_in(user, make_user):
    p1 = user.post("/projects", json={"name": "P1"}).json()
    _k, h = _key(user)
    r = user.c.delete(f"/api/v1/projects/{p1['id']}", params={"confirm": "P1"}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "deleted"

    p2 = user.post("/projects", json={"name": "P2"}).json()
    k2, h2 = _key(user, name="guarded")
    assert user.patch(f"/api-keys/{k2['id']}", json={"requires_approval": True}).json()["requires_approval"] is True
    r = user.c.delete(f"/api/v1/projects/{p2['id']}", params={"confirm": "P2"}, headers=h2)
    assert r.status_code == 202 and r.json()["status"] == "pending_approval"


def test_database_delete_gated_and_executed_through_normal_path(user, project):
    d = user.post("/databases", json={"project_id": project["id"], "name": "agentdb"})
    assert d.status_code == 202, d.text
    db_id = d.json()["id"]
    _k, h = _key(user, kind="agent")
    r = user.c.delete(f"/api/v1/databases/{db_id}", params={"confirm": "agentdb"}, headers=h)
    assert r.status_code == 202 and r.json()["status"] == "pending_approval"
    assert user.get(f"/databases/{db_id}").json()["status"] != "deleting"
    appr = r.json()["approval"]
    ok = user.post(f"/approvals/{appr['id']}/approve", json={})
    assert ok.status_code == 200 and ok.json()["status"] == "executed", ok.text
    assert user.get(f"/databases/{db_id}").json()["status"] == "deleting"
    with system_session() as db:
        job = db.execute(
            text("SELECT kind, payload->>'reason' FROM provisioning_jobs WHERE database_id = :d AND kind = 'delete'"),
            {"d": db_id},
        ).one()
    assert appr["id"] in job[1]


def test_failed_execution_is_recorded_and_leaves_state_intact(user, project):
    _k, h = _key(user, kind="agent")
    appr = user.c.delete(f"/api/v1/projects/{project['id']}", params={"confirm": project["name"]}, headers=h).json()[
        "approval"
    ]
    # The project stops being empty before the owner approves.
    assert user.post("/databases", json={"project_id": project["id"], "name": "late"}).status_code == 202
    r = user.post(f"/approvals/{appr['id']}/approve", json={})
    assert r.status_code == 200
    assert r.json()["status"] == "failed" and r.json()["result"]["error"] == "project_not_empty"
    assert user.get(f"/projects/{project['id']}").status_code == 200


def test_expired_requests_cannot_be_approved(user, project):
    _k, h = _key(user, kind="agent")
    appr = user.c.delete(f"/api/v1/projects/{project['id']}", params={"confirm": project["name"]}, headers=h).json()[
        "approval"
    ]
    with system_session() as db:
        db.execute(
            text("UPDATE approval_requests SET expires_at = now() - interval '1 minute' WHERE id = :i"),
            {"i": appr["id"]},
        )
    assert user.get(f"/approvals/{appr['id']}").json()["status"] == "expired"
    assert user.post(f"/approvals/{appr['id']}/approve", json={}).status_code == 409
    assert user.get(f"/projects/{project['id']}").status_code == 200


def test_approvals_are_tenant_and_key_isolated(user, make_user, project):
    _k, h = _key(user, kind="agent")
    appr = user.c.delete(f"/api/v1/projects/{project['id']}", params={"confirm": project["name"]}, headers=h).json()[
        "approval"
    ]
    other = make_user()
    assert other.get(f"/approvals/{appr['id']}").status_code == 404
    assert other.post(f"/approvals/{appr['id']}/approve", json={}).status_code == 404
    assert other.get("/approvals").json()["total"] == 0
    # A second key of the same user can't see the first key's request.
    _k2, h2 = _key(user, kind="agent", name="other-bot")
    assert user.c.get(f"/api/v1/approvals/{appr['id']}", headers=h2).status_code == 404
    assert user.c.get("/api/v1/approvals", headers=h2).json()["total"] == 0
    assert user.get(f"/projects/{project['id']}").status_code == 200


def test_per_key_activity_and_audit_filter(user, project):
    from app.monitoring.metrics import flush_api_counters

    k, h = _key(user, kind="agent", name="reporter")
    for _ in range(3):
        assert user.c.get("/api/v1/projects", headers=h).status_code == 200
    assert user.c.patch(f"/api/v1/projects/{project['id']}", json={"description": "x"}, headers=h).status_code == 200
    with system_session() as db:
        flush_api_counters(db)
    rows = user.get("/api-keys/activity", params={"kind": "agent"}).json()
    row = next(r for r in rows if r["key"]["id"] == k["id"])
    assert row["requests_24h"] >= 4 and row["hourly"][-1] >= 4
    assert row["actions_7d"] >= 1 and row["last_action"] == "project.update"
    feed = user.get("/activity", params={"api_key_id": k["id"]}).json()
    assert feed["total"] >= 1 and all(e["actor_api_key_id"] == k["id"] for e in feed["items"])

    with system_session() as db:
        from app.models import ApiKeyRequestCounter

        c = db.scalar(select(ApiKeyRequestCounter).where(ApiKeyRequestCounter.api_key_id == k["id"]))
        assert c is not None and str(c.user_id) == user.user["id"]
        assert c.hour <= dt.datetime.now(dt.UTC)


def test_pending_requests_per_key_are_capped(user, monkeypatch, project):
    from app.services import approvals
    from tests.test_backups import set_plan

    set_plan(user.user["id"], "pro")
    monkeypatch.setattr(approvals, "MAX_PENDING_PER_KEY", 1)
    _k, h = _key(user, kind="agent")
    d1 = user.post("/databases", json={"project_id": project["id"], "name": "capdb1"}).json()
    d2 = user.post("/databases", json={"project_id": project["id"], "name": "capdb2"}).json()
    first = user.c.delete(f"/api/v1/databases/{d1['id']}", params={"confirm": "capdb1"}, headers=h)
    assert first.status_code == 202
    # Same resource again: reuses the pending request, not counted twice.
    assert user.c.delete(f"/api/v1/databases/{d1['id']}", params={"confirm": "capdb1"}, headers=h).status_code == 202
    second = user.c.delete(f"/api/v1/databases/{d2['id']}", params={"confirm": "capdb2"}, headers=h)
    assert second.status_code == 429
    assert user.get("/approvals").json()["total"] == 1

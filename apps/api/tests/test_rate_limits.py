"""Per-credential API budgets and sensitive-action limits."""

from __future__ import annotations

from app.core.config import get_settings


def test_api_key_budget_is_enforced_per_key(user, monkeypatch):
    k1 = user.post("/api-keys", json={"name": "a", "scopes": ["projects:read"]}).json()
    k2 = user.post("/api-keys", json={"name": "b", "scopes": ["projects:read"]}).json()
    monkeypatch.setattr(get_settings(), "api_rate_per_minute_api_key", 3)
    h1 = {"Authorization": f"Bearer {k1['key']}"}
    codes = [user.c.get("/api/v1/projects", headers=h1).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3] == 429
    r = user.c.get("/api/v1/projects", headers=h1)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    assert r.json()["error"]["code"] == "rate_limited"
    # Another key of the same user has its own budget.
    assert user.c.get("/api/v1/projects", headers={"Authorization": f"Bearer {k2['key']}"}).status_code == 200


def test_write_budget_applies_to_sessions(user, monkeypatch):
    monkeypatch.setattr(get_settings(), "api_write_rate_per_minute", 2)
    codes = [user.post("/projects", json={"name": f"p{i}"}).status_code for i in range(3)]
    assert codes[-1] == 429
    assert user.get("/projects").status_code == 200  # reads still work


def test_api_key_creation_is_rate_limited(make_user):
    u = make_user()
    codes = []
    for i in range(22):
        r = u.post("/api-keys", json={"name": f"k{i}", "scopes": ["projects:read"]})
        codes.append(r.status_code)
        if r.status_code == 201:
            u.delete(f"/api-keys/{r.json()['id']}")
    assert 429 in codes and codes.index(429) == 20

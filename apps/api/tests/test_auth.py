from __future__ import annotations

import uuid

from tests.conftest import ApiClient, deliver_emails, last_token_for


def _register(c: ApiClient, email: str, password: str = "a-Very-good-passphrase-42"):
    return c.post("/auth/register", json={"email": email, "password": password})


def test_register_requires_verification_and_does_not_enumerate(app, user):
    c = ApiClient(app)
    new = f"new-{uuid.uuid4().hex[:6]}@example.com"
    r1 = _register(c, new)
    r2 = _register(ApiClient(app), user.user["email"])  # already registered
    assert r1.status_code == r2.status_code == 202
    assert r1.json() == r2.json()


def test_weak_password_rejected(app):
    r = _register(ApiClient(app), "weak@example.com", "short")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "weak_password"


def test_email_verification_token_single_use(app):
    c = ApiClient(app)
    addr = f"v-{uuid.uuid4().hex[:6]}@example.com"
    _register(c, addr)
    deliver_emails()
    tok = last_token_for(addr)
    assert c.post("/auth/verify-email", json={"token": tok}).status_code == 200
    r = ApiClient(app).post("/auth/verify-email", json={"token": tok})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_token"


def test_login_errors_are_generic(app, user):
    c = ApiClient(app)
    wrong_pw = c.post("/auth/login", json={"email": user.user["email"], "password": "nope-nope-nope"})
    no_user = c.post("/auth/login", json={"email": "nobody@example.com", "password": "nope-nope-nope"})
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json()["error"]["message"] == no_user.json()["error"]["message"]
    assert wrong_pw.json()["error"]["code"] == no_user.json()["error"]["code"] == "invalid_credentials"


def test_login_logout_and_session_revocation(app, user):
    c = ApiClient(app)
    r = c.post("/auth/login", json={"email": user.user["email"], "password": user.password})
    assert r.status_code == 200
    assert c.get("/auth/me").status_code == 200
    sessions = user.get("/auth/sessions").json()
    assert len(sessions) >= 2
    # revoke the other session from the first client
    other = next(s for s in sessions if not s["current"])
    assert user.delete(f"/auth/sessions/{other['id']}").status_code == 200
    assert c.get("/auth/me").status_code == 401
    assert user.post("/auth/logout").status_code == 200
    assert user.get("/auth/me").status_code == 401


def test_progressive_login_throttling(app, user):
    c = ApiClient(app)
    codes = [
        c.post("/auth/login", json={"email": user.user["email"], "password": f"bad-password-{i}"}).status_code
        for i in range(6)
    ]
    assert codes[:5] == [401] * 5
    assert codes[5] == 429
    # Even the right password is blocked during the lockout window.
    r = c.post("/auth/login", json={"email": user.user["email"], "password": user.password})
    assert r.status_code == 429 and "Retry-After" in r.headers


def test_password_reset_single_use_and_revokes_sessions(app, user):
    anon = ApiClient(app)
    assert anon.post("/auth/password/forgot", json={"email": user.user["email"]}).status_code == 202
    assert anon.post("/auth/password/forgot", json={"email": "ghost@example.com"}).status_code == 202
    deliver_emails()
    tok = last_token_for(user.user["email"])
    r = anon.post("/auth/password/reset", json={"token": tok, "password": "a-brand-new-passphrase-7"})
    assert r.status_code == 200
    assert user.get("/auth/me").status_code == 401  # old session revoked
    r = anon.post("/auth/password/reset", json={"token": tok, "password": "another-passphrase-88"})
    assert r.status_code == 400
    c = ApiClient(app)
    assert (
        c.post("/auth/login", json={"email": user.user["email"], "password": "a-brand-new-passphrase-7"}).status_code
        == 200
    )


def test_change_password_requires_current(app, user):
    r = user.post(
        "/auth/password/change", json={"current_password": "wrong-one-123", "new_password": "x-new-pass-12345"}
    )
    assert r.status_code == 400
    r = user.post("/auth/password/change", json={"current_password": user.password, "new_password": "x-new-pass-12345"})
    assert r.status_code == 200
    assert user.get("/auth/me").status_code == 200  # current session kept


def test_csrf_required_for_cookie_auth(app, user):
    r = user.c.post("/api/v1/projects", json={"name": "x"}, headers={"Origin": "http://localhost:3000"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    tok = user.c.cookies.get("dmx_csrf")
    r = user.c.post(
        "/api/v1/projects", json={"name": "x"}, headers={"X-CSRF-Token": tok, "Origin": "https://evil.example"}
    )
    assert r.status_code == 403
    forged = "abc.def"
    user.c.cookies.set("dmx_csrf", forged)
    r = user.c.post("/api/v1/projects", json={"name": "x"}, headers={"X-CSRF-Token": forged})
    assert r.status_code == 403


def test_google_disabled_gracefully(app):
    c = ApiClient(app)
    assert c.get("/auth/providers").json() == {"password": True, "google": False}
    r = c.get("/auth/google/start", follow_redirects=False)
    assert r.status_code == 404 and r.json()["error"]["code"] == "provider_disabled"


def test_unverified_user_cannot_create_database(make_user):
    c = make_user(verified=False)
    p = c.post("/projects", json={"name": "p1"}).json()
    r = c.post("/databases", json={"project_id": p["id"], "name": "db"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "email_not_verified"


def test_register_rate_limited(app):
    c = ApiClient(app)
    codes = [
        c.post(
            "/auth/register", json={"email": "same@example.com", "password": "a-Very-good-passphrase-42"}
        ).status_code
        for _ in range(4)
    ]
    assert codes[-1] == 429


def test_reauthentication(app, user):
    assert user.post("/auth/reauthenticate", json={"password": "wrong-password"}).status_code == 400
    assert user.post("/auth/reauthenticate", json={"password": user.password}).status_code == 200


def test_security_headers_and_request_id(app, user):
    r = user.get("/auth/me", headers={"X-Request-ID": "test-request-123"})
    assert r.headers["X-Request-ID"] == "test-request-123"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Cache-Control"] == "no-store"
    r = user.get("/does-not-exist")
    assert r.status_code == 404 and r.json()["error"]["request_id"]


def test_request_body_size_limit(app, user):
    r = user.post("/projects", content=b"{" + b" " * 2_000_000 + b"}", headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_account_deletion(app, make_user):
    c = make_user()
    r = c.delete("/account", json={"confirm": "wrong@example.com"})
    assert r.status_code == 400
    r = c.delete("/account", json={"confirm": c.user["email"]})
    assert r.status_code == 200
    assert c.get("/auth/me").status_code == 401
    assert (
        ApiClient(app).post("/auth/login", json={"email": c.user["email"], "password": c.password}).status_code == 401
    )

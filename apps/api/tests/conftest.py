"""Test fixtures.

Uses a dedicated PostgreSQL database (`dmx_test`, recreated per session and migrated with
Alembic) and Redis DB 15. Integration tests additionally need the provisioner and host agent
(PROVISIONER_URL) and start their own internal API + gateway against the test database.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PG_ADMIN_URL = os.environ.get("DMX_TEST_PG_ADMIN", "postgresql://postgres@127.0.0.1:55432/postgres")
TEST_DB = os.environ.get("DMX_TEST_DB", "dmx_test")

os.environ.update(
    {
        "DMX_ENV": "test",
        "DATABASE_URL": f"postgresql+psycopg://dmx_app:dmx_app@127.0.0.1:55432/{TEST_DB}",
        "MIGRATIONS_DATABASE_URL": f"postgresql+psycopg://dmx_owner:dmx_owner@127.0.0.1:55432/{TEST_DB}",
        "REDIS_URL": os.environ.get("DMX_TEST_REDIS", "redis://127.0.0.1:56379/15"),
        "DMX_EMAIL_BACKEND": "memory",
        "DMX_PUBLIC_WEB_URL": "http://localhost:3000",
        "DMX_CORS_ORIGINS": "http://localhost:3000",
        "DMX_ARGON2_MEMORY_KIB": "8192",
        "DMX_ARGON2_TIME_COST": "1",
        "DMX_JOB_BACKOFF_BASE_SECONDS": "0",
        "DMX_LOG_LEVEL": "INFO",
    }
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# setdefault: this module may be imported twice (as `conftest` and `tests.conftest`); ports must agree.
GATEWAY_PORT = int(os.environ.setdefault("DMX_TEST_GATEWAY_PORT", str(_free_port())))
INTERNAL_PORT = int(os.environ.setdefault("DMX_TEST_INTERNAL_PORT", str(_free_port())))
GATEWAY_STATUS_PORT = int(os.environ.setdefault("DMX_TEST_GATEWAY_STATUS_PORT", str(_free_port())))
os.environ.update(
    {
        "DMX_GATEWAY_INTERNAL_HOST": "127.0.0.1",
        "DMX_GATEWAY_INTERNAL_PORT": str(GATEWAY_PORT),
        "DMX_DB_PUBLIC_PORT": str(GATEWAY_PORT),
        "DMX_DB_PUBLIC_HOST": "localhost",
        "DMX_DB_HOST_SUFFIX": "db.localhost",
        "DMX_GATEWAY_CA_FILE": str(ROOT / ".dev/certs/ca.crt"),
    }
)


def _create_test_database() -> None:
    import psycopg

    with psycopg.connect(PG_ADMIN_URL, autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
            (TEST_DB,),
        )
        conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}"')
        conn.execute(f'CREATE DATABASE "{TEST_DB}" OWNER dmx_owner')
        conn.execute(f'REVOKE ALL ON DATABASE "{TEST_DB}" FROM PUBLIC')
        conn.execute(f'GRANT CONNECT ON DATABASE "{TEST_DB}" TO dmx_app')
    with psycopg.connect(PG_ADMIN_URL.rsplit("/", 1)[0] + f"/{TEST_DB}", autocommit=True) as conn:
        conn.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        conn.execute("ALTER SCHEMA public OWNER TO dmx_owner")
        conn.execute("GRANT USAGE ON SCHEMA public TO dmx_app")
        conn.execute(
            "ALTER DEFAULT PRIVILEGES FOR ROLE dmx_owner IN SCHEMA public "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO dmx_app"
        )
        conn.execute(
            "ALTER DEFAULT PRIVILEGES FOR ROLE dmx_owner IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO dmx_app"
        )


@pytest.fixture(scope="session", autouse=True)
def database() -> Iterator[None]:
    _create_test_database()
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT / "apps/api",
        check=True,
        capture_output=True,
        env=os.environ.copy(),
    )
    yield


@pytest.fixture(autouse=True)
def _clean_state(database: None) -> Iterator[None]:
    from app.core.redis import get_redis
    from app.services import email

    get_redis().flushdb()
    email.SENT.clear()
    # Jobs queued by earlier (non-integration) tests must not be picked up by later ones.
    from sqlalchemy import text

    from app.core.db import system_session

    with system_session() as db:
        db.execute(text("UPDATE provisioning_jobs SET status = 'cancelled' WHERE status IN ('queued', 'running')"))
    yield


@pytest.fixture
def app():
    from app.main import app as fastapi_app

    return fastapi_app


class ApiClient:
    """TestClient wrapper that handles the CSRF double-submit cookie like the web app does."""

    def __init__(self, app, origin: str = "http://localhost:3000"):
        from fastapi.testclient import TestClient

        self.c = TestClient(app, base_url="http://testserver")
        self.origin = origin
        self.c.get("/api/v1/auth/csrf")
        self.user: dict | None = None
        self.password: str | None = None

    def _headers(self, extra: dict | None = None) -> dict:
        h = {"Origin": self.origin}
        tok = self.c.cookies.get("dmx_csrf")
        if tok:
            h["X-CSRF-Token"] = tok
        h.update(extra or {})
        return h

    def get(self, path, **kw):
        return self.c.get("/api/v1" + path, **kw)

    def post(self, path, json=None, headers=None, **kw):
        return self.c.post("/api/v1" + path, json=json, headers=self._headers(headers), **kw)

    def patch(self, path, json=None, headers=None, **kw):
        return self.c.patch("/api/v1" + path, json=json, headers=self._headers(headers), **kw)

    def delete(self, path, headers=None, **kw):
        return self.c.request("DELETE", "/api/v1" + path, headers=self._headers(headers), **kw)


def last_token_for(email_addr: str) -> str:
    from app.services import email

    for m in reversed(email.SENT):
        if m["to"] == email_addr:
            match = re.search(r"token=([A-Za-z0-9_\-]+)", m["text"])
            if match:
                return match.group(1)
    raise AssertionError(f"no token email for {email_addr}")


def deliver_emails() -> None:
    from app.core.db import system_session
    from app.services import email

    with system_session() as db:
        email.deliver_pending(db, limit=100)


@pytest.fixture
def make_user(app):
    def _make(verified: bool = True, password: str = "a-Very-good-passphrase-42") -> ApiClient:
        c = ApiClient(app)
        addr = f"user-{uuid.uuid4().hex[:10]}@example.com"
        r = c.post("/auth/register", json={"email": addr, "password": password})
        assert r.status_code == 202, r.text
        deliver_emails()
        if verified:
            r = c.post("/auth/verify-email", json={"token": last_token_for(addr)})
            assert r.status_code == 200, r.text
        else:
            r = c.post("/auth/login", json={"email": addr, "password": password})
            assert r.status_code == 200, r.text
        c.user = c.get("/auth/me").json()
        c.password = password
        return c

    return _make


@pytest.fixture
def user(make_user) -> ApiClient:
    return make_user()


@pytest.fixture
def project(user) -> dict:
    r = user.post("/projects", json={"name": "Test project", "environment": "development"})
    assert r.status_code == 201, r.text
    return r.json()


# --- integration infrastructure --------------------------------------------------------------------


def _provisioner_available() -> bool:
    from app.provisioning.client import ProvisionerClient, ProvisionerError

    try:
        ProvisionerClient(timeout=2).health()
        return True
    except ProvisionerError:
        return False


@pytest.fixture(scope="session")
def data_plane(database) -> Iterator[dict]:
    """Starts an internal API (against the test DB) and a gateway; requires a running provisioner."""
    if not _provisioner_available():
        pytest.skip("provisioner not running (deploy on a host prepared by infra/scripts/host-setup.sh)")
    import uvicorn

    from app.internal_main import app as internal_app

    config = uvicorn.Config(internal_app, host="127.0.0.1", port=INTERNAL_PORT, log_level="warning")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    env = os.environ.copy()
    env.update(
        {
            "DMX_GATEWAY_PORT": str(GATEWAY_PORT),
            "DMX_GATEWAY_HOST": "127.0.0.1",
            "DMX_GATEWAY_STATUS_PORT": str(GATEWAY_STATUS_PORT),
            "DMX_GATEWAY_CERT": str(ROOT / ".dev/certs/gateway.crt"),
            "DMX_GATEWAY_KEY": str(ROOT / ".dev/certs/gateway.key"),
            "DMX_INTERNAL_API_URL": f"http://127.0.0.1:{INTERNAL_PORT}",
            "DMX_DB_HOST_SUFFIX": "db.localhost",
            "DMX_GATEWAY_ROUTE_TTL": "0.5",
        }
    )
    gw = subprocess.Popen(
        [str(ROOT / "apps/gateway/.venv/bin/python"), "-m", "dmx_gateway.server"],
        cwd=ROOT / "apps/gateway",
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", GATEWAY_PORT), timeout=0.2).close()
            socket.create_connection(("127.0.0.1", INTERNAL_PORT), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield {"gateway_port": GATEWAY_PORT, "gateway_proc": gw}
    gw.terminate()
    out, _ = gw.communicate(timeout=5)
    if os.environ.get("DMX_TEST_DEBUG"):
        print("\n--- gateway output ---\n" + out.decode(errors="replace")[-5000:])
    server.should_exit = True


def run_jobs(max_iterations: int = 20) -> list[str]:
    """Run queued provisioning jobs inline (what the worker does)."""
    from app.workers import jobs

    results = []
    wid = "test-worker"
    for _ in range(max_iterations):
        jid = jobs.claim_next(wid)
        if jid is None:
            break
        results.append(jobs.execute(jid, wid))
    return results

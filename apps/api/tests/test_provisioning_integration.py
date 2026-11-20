"""End-to-end provisioning against the real data plane: provisioner, host agent, customer
PostgreSQL systemd units and the TLS gateway. Skipped when the provisioner isn't running (run on
a host prepared by infra/scripts/host-setup.sh with the provisioner deployed)."""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import subprocess
import urllib.parse

import psycopg
import pytest
from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.db import system_session
from app.models import Database, DatabaseCredential, ProvisioningJob, Secret
from tests.conftest import ROOT, run_jobs

pytestmark = pytest.mark.integration
CA = str(ROOT / ".dev/certs/ca.crt")


UNIT_PROPS = (
    "ActiveState,User,MemoryMax,CPUQuotaPerSecUSec,TasksMax,NoNewPrivileges,ProtectSystem,PrivateDevices,"
    "CapabilityBoundingSet,IPAddressDeny,ExecStart,DropInPaths,Slice"
)
DATA_PLANE = os.environ.get("DMX_TEST_DATA_PLANE", "/var/lib/databasemax/data-plane")


def _unit(iid: str) -> dict[str, str]:
    """Properties of the instance's systemd unit (empty DropInPaths: the instance doesn't exist)."""
    out = subprocess.run(
        ["systemctl", "show", f"dmx-pg@{iid}.service", f"--property={UNIT_PROPS}"], capture_output=True, text=True
    ).stdout
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


def _exists(iid: str) -> bool:
    return bool(_unit(iid).get("DropInPaths"))


def _gone(iid: str) -> bool:
    """No unit, no filesystems and no system user left for the instance."""
    user = "dmxdb-" + hashlib.sha256(iid.encode()).hexdigest()[:12]
    no_user = subprocess.run(["getent", "passwd", user], capture_output=True).returncode != 0
    no_data = not any(os.path.exists(f"{DATA_PLANE}/mnt/{iid}{s}") for s in ("", "-wal", "-sys"))
    return not _exists(iid) and no_user and no_data


def _instance(database_id: str) -> str:
    with system_session() as db:
        return db.get(Database, database_id).internal_database_name


def _connect(url: str, gateway_port: int, *, verify: bool = True, sni: bool = True) -> psycopg.Connection:
    parts = urllib.parse.urlsplit(url)
    params = {
        "host": parts.hostname if sni else "127.0.0.1",
        "hostaddr": "127.0.0.1",
        "port": gateway_port,
        "user": urllib.parse.unquote(parts.username),
        "password": urllib.parse.unquote(parts.password or ""),
        "dbname": parts.path.lstrip("/"),
        "connect_timeout": 5,
    }
    if verify and sni:
        params.update(sslmode="verify-full", sslrootcert=CA)
    else:
        params.update(sslmode="require")
    return psycopg.connect(**params, autocommit=True)


def _create_ready(user, project, name="primary") -> dict:
    r = user.post(
        "/databases",
        json={"project_id": project["id"], "name": name},
        headers={"Idempotency-Key": f"idem-{name}-00001"},
    )
    assert r.status_code == 202, r.text
    d = r.json()
    # Retrying the same request must not create anything new.
    r2 = user.post(
        "/databases",
        json={"project_id": project["id"], "name": name},
        headers={"Idempotency-Key": f"idem-{name}-00001"},
    )
    assert r2.json()["id"] == d["id"]
    results = run_jobs()
    assert results == ["succeeded"], results
    d = user.get(f"/databases/{d['id']}").json()
    assert d["status"] == "ready", d
    return d


def test_full_lifecycle(data_plane, user, project):
    port = data_plane["gateway_port"]
    d = _create_ready(user, project)
    iid = _instance(d["id"])
    # Instance hardening as deployed: own system user, cgroup limits, no privileges, read-only
    # system, no outbound network, listening on loopback only.
    u = _unit(iid)
    assert u["ActiveState"] == "active" and u["Slice"] == "dmx-customers.slice"
    assert u["User"] == "dmxdb-" + hashlib.sha256(iid.encode()).hexdigest()[:12]
    assert u["MemoryMax"] == str(256 * 1024 * 1024) and u["CPUQuotaPerSecUSec"] == "250ms" and u["TasksMax"] == "256"
    assert u["NoNewPrivileges"] == "yes" and u["ProtectSystem"] == "strict" and u["PrivateDevices"] == "yes"
    assert u["CapabilityBoundingSet"] == ""
    assert "0.0.0.0/0" in u["IPAddressDeny"] and "::/0" in u["IPAddressDeny"]
    assert "listen_addresses=127.0.0.1 " in u["ExecStart"] and "ssl=off" in u["ExecStart"]

    conn_info = user.get(f"/databases/{d['id']}/connection").json()
    assert conn_info["password"] is None and "••••" in conn_info["url"]
    conn_info = user.get(f"/databases/{d['id']}/connection", params={"reveal": "true"}).json()
    url = conn_info["url"]
    assert url.startswith("postgresql://u_") and url.endswith("?sslmode=require")
    assert conn_info["host"] == f"{d['host']}"

    with _connect(url, port) as c:
        c.execute("CREATE TABLE items (id serial primary key, name text)")
        c.execute("INSERT INTO items (name) VALUES ('a'), ('b')")
        assert c.execute("SELECT count(*) FROM items").fetchone()[0] == 2
        assert c.info.pgconn.ssl_in_use
        row = c.execute(
            "SELECT rolsuper, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
        assert row == (False, False, False)
    # Without SNI (e.g. IP host) the username alone routes correctly.
    with _connect(url, port, sni=False) as c:
        assert c.execute("SELECT count(*) FROM items").fetchone()[0] == 2
    # Plaintext is refused by the gateway.
    with pytest.raises(psycopg.OperationalError, match="TLS is required"):
        p = urllib.parse.urlsplit(url)
        psycopg.connect(
            host="127.0.0.1",
            port=port,
            user=p.username,
            password=urllib.parse.unquote(p.password),
            dbname="primary",
            sslmode="disable",
            connect_timeout=5,
        )

    # Rotation: old password stops working, existing sessions are terminated, new one works.
    old = _connect(url, port)
    r = user.post(f"/databases/{d['id']}/credentials/rotate")
    assert r.status_code == 202
    assert run_jobs() == ["succeeded"]
    with pytest.raises(psycopg.OperationalError):
        old.execute("SELECT 1")
    with pytest.raises(psycopg.OperationalError, match="password authentication failed"):
        _connect(url, port)
    new_url = user.get(f"/databases/{d['id']}/connection", params={"reveal": "true"}).json()["url"]
    assert new_url != url
    with _connect(new_url, port) as c:
        assert c.execute("SELECT count(*) FROM items").fetchone()[0] == 2
    with system_session() as db:
        creds = db.scalars(select(DatabaseCredential).where(DatabaseCredential.database_id == d["id"])).all()
        assert sum(1 for c in creds if c.revoked_at is None) == 1

    # Stop / start.
    assert user.post(f"/databases/{d['id']}/stop").status_code == 202
    assert run_jobs() == ["succeeded"]
    assert user.get(f"/databases/{d['id']}").json()["status"] == "stopped"
    import time

    time.sleep(1)  # gateway route cache TTL in tests is 0.5s
    with pytest.raises(psycopg.OperationalError, match="stopped"):
        _connect(new_url, port)
    assert user.post(f"/databases/{d['id']}/start").status_code == 202
    assert run_jobs() == ["succeeded"]
    assert user.get(f"/databases/{d['id']}").json()["status"] == "ready"
    time.sleep(1)
    with _connect(new_url, port) as c:
        assert c.execute("SELECT count(*) FROM items").fetchone()[0] == 2  # data persisted across restart

    # Measured usage from the real instance.
    from app.monitoring import metrics
    from app.provisioning.client import ProvisionerClient

    with system_session() as db:
        assert metrics.collect_all(db, ProvisionerClient()) >= 1
    usage = user.get("/usage/current").json()
    assert usage["storage_used_bytes"] > 1_000_000 and usage["measured_at"]
    series = user.get(f"/databases/{d['id']}/usage", params={"metric": "storage_bytes"}).json()
    assert series["points"] and series["source"] == "measured"

    # Deletion removes the instance, the volume and the credentials.
    assert user.post("/auth/reauthenticate", json={"password": user.password}).status_code == 200
    assert user.delete(f"/databases/{d['id']}", params={"confirm": "primary"}).status_code == 202
    assert run_jobs() == ["succeeded"]
    assert user.get(f"/databases/{d['id']}").status_code == 404
    assert _gone(iid)
    time.sleep(1)
    with pytest.raises(psycopg.OperationalError):
        _connect(new_url, port)
    with system_session() as db:
        secrets = db.scalars(
            select(Secret)
            .join(DatabaseCredential, DatabaseCredential.secret_reference == Secret.id)
            .where(DatabaseCredential.database_id == d["id"])
        ).all()
        assert all(s.destroyed_at is not None and s.ciphertext == b"" for s in secrets)


def test_failed_provisioning_cleans_up(data_plane, user, project, monkeypatch):
    # Break the connectivity check (wrong gateway port) so every attempt fails after the
    # instance was created; after the final attempt nothing may be left behind.
    monkeypatch.setenv("DMX_GATEWAY_INTERNAL_PORT", "1")
    monkeypatch.setenv("DMX_JOB_MAX_ATTEMPTS", "2")
    get_settings.cache_clear()
    try:
        r = user.post("/databases", json={"project_id": project["id"], "name": "doomed"})
        d = r.json()
        iid = _instance(d["id"])
        import app.provisioning.connectivity as conn_mod

        monkeypatch.setattr(conn_mod, "verify", _fast_fail)
        import app.workers.jobs as jobs_mod

        monkeypatch.setattr(jobs_mod, "verify", _fast_fail)
        results = run_jobs()
        assert results == ["queued", "failed"], results
    finally:
        get_settings.cache_clear()
    d = user.get(f"/databases/{d['id']}").json()
    assert d["status"] == "failed"
    assert d["provisioning"]["error_code"] == "connectivity_failed"
    assert _gone(iid)
    notes = user.get("/notifications").json()["items"]
    assert any(n["type"] == "database_failed" for n in notes)
    # A retry after the problem is fixed succeeds.
    monkeypatch.undo()
    get_settings.cache_clear()
    assert user.post(f"/databases/{d['id']}/retry").status_code == 202
    assert run_jobs() == ["succeeded"]
    assert user.get(f"/databases/{d['id']}").json()["status"] == "ready"
    _cleanup(user, d)


def _fast_fail(*a, **k):
    from app.provisioning.connectivity import ConnectivityError

    raise ConnectivityError("simulated")


def test_worker_crash_is_recovered_without_duplicates(data_plane, user, project):
    from app.workers import jobs

    r = user.post("/databases", json={"project_id": project["id"], "name": "crashy"})
    d = r.json()
    jid = jobs.claim_next("crashed-worker")
    assert jid is not None
    # Simulate a worker that died after creating the instance but before finishing.
    from app.provisioning.client import ProvisionerClient

    with system_session() as db:
        database = db.get(Database, d["id"])
        ProvisionerClient().ensure_instance(
            database.internal_database_name,
            pg_version="16",
            profile="nano",
            storage_mb=500,
            max_connections=20,
            database_id=str(database.id),
            owner_id=str(database.owner_id),
        )
        database.status = "provisioning"
        db.execute(
            update(ProvisioningJob)
            .where(ProvisioningJob.id == jid)
            .values(lease_expires_at=dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1))
        )
    # Another worker reclaims the expired lease and completes idempotently.
    assert run_jobs() == ["succeeded"]
    iid = _instance(d["id"])
    assert _exists(iid) and _unit(iid)["ActiveState"] == "active"
    with system_session() as db:
        job = db.get(ProvisioningJob, jid)
        assert job.status == "succeeded" and job.attempt_count == 2
    _cleanup(user, user.get(f"/databases/{d['id']}").json())


def test_sni_must_match_username(data_plane, make_user):
    port = data_plane["gateway_port"]
    a = make_user()
    pa = a.post("/projects", json={"name": "a"}).json()
    da = _create_ready(a, pa, "alpha")
    ua = a.get(f"/databases/{da['id']}/connection", params={"reveal": "true"}).json()["url"]
    b = make_user()
    pb = b.post("/projects", json={"name": "b"}).json()
    db_ = _create_ready(b, pb, "beta")
    # Use tenant A's credentials with tenant B's hostname: rejected by the gateway.
    pa_ = urllib.parse.urlsplit(ua)
    with pytest.raises(psycopg.OperationalError, match="unknown endpoint"):
        psycopg.connect(
            host=db_["host"],
            hostaddr="127.0.0.1",
            port=port,
            user=pa_.username,
            password=urllib.parse.unquote(pa_.password),
            dbname="alpha",
            sslmode="verify-full",
            sslrootcert=CA,
            connect_timeout=5,
        )
    _cleanup(a, da)
    _cleanup(b, db_)


def _cleanup(client, d):
    client.post("/auth/reauthenticate", json={"password": client.password})
    client.delete(f"/databases/{d['id']}", params={"confirm": d["name"]})
    run_jobs()

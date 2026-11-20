"""Two data-plane hosts on one machine: the deployed provisioner plus a second provisioner started
here with its own state directory and port range (sharing the machine's host agent), listening on
127.0.0.1 like a worker host listens on its private address. Covers placement, drain, cross-host
clone (backup relay), explicit move with unchanged URL/credentials, and host failure visibility
without any silent reassignment. Runs as root on a host prepared by host-setup.sh."""

from __future__ import annotations

import datetime as dt
import os
import secrets
import socket
import subprocess
import time
import uuid
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import select, update

from app.core.db import system_session
from app.models import Database, InfrastructureHost, ProvisioningJob, utcnow
from app.monitoring.metrics import update_host_inventory
from app.provisioning.hosts import ProvisionerPool, bootstrap_host, register_host
from app.workers.reconcile import reconcile
from tests.conftest import ROOT, run_jobs
from tests.test_admin import _make_admin
from tests.test_backups import set_plan
from tests.test_provisioning_integration import _connect

pytestmark = pytest.mark.integration


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _unit_exists(iid: str) -> bool:
    out = subprocess.run(["systemctl", "show", f"dmx-pg@{iid}.service", "--property=DropInPaths"],
                         capture_output=True, text=True).stdout  # fmt: skip
    return out.strip() not in ("", "DropInPaths=")


@pytest.fixture(scope="module")
def second_host(data_plane, tmp_path_factory):
    name = "test-host-2"
    token = secrets.token_urlsafe(32)
    state_dir = tmp_path_factory.mktemp("host2-state")
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith("DMX_") and k not in ("PROVISIONER_TOKEN",)}
    env.update(
        {
            "DMX_ENV": "test",
            "PROVISIONER_TOKEN": token,
            "PROVISIONER_ADMIN_SECRET": secrets.token_urlsafe(32),
            "DMX_HOST_NAME": name,
            "DMX_REGION": "oci-local-1",
            "DMX_INSTANCE_LISTEN": "127.0.0.1",
            "DMX_INSTANCE_PORT_MIN": "26000",
            "DMX_INSTANCE_PORT_MAX": "26099",
            "DMX_INSTANCE_CLIENT_CIDRS": "127.0.0.1/32",
            "DMX_STORAGE_BACKEND": os.environ.get("DMX_TEST_STORAGE_BACKEND", "dir"),
            "DMX_PROVISIONER_STATE_DIR": str(state_dir),
            "DMX_BACKUP_DIR": str(tmp_path_factory.mktemp("host2-backups")),
            "DMX_DATA_PLANE_MEMORY_MB": "2048",
            "DMX_PG_VERSIONS": "16",
        }
    )
    proc = subprocess.Popen(
        [str(ROOT / "apps/provisioner/.venv/bin/uvicorn"), "dmx_provisioner.main:app", "--port", str(port)],
        cwd=ROOT / "apps/provisioner",
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    endpoint = f"http://127.0.0.1:{port}"
    with system_session() as db:
        update_host_inventory(db, ProvisionerPool())  # bootstrap host
        host = register_host(db, name=name, endpoint=endpoint, region="oci-local-1", token=token)
        host_id = host.id
    with system_session() as db:
        update_host_inventory(db, ProvisionerPool())
        boot = bootstrap_host(db)
        assert boot is not None
        boot_id = boot.id
    yield {"id": host_id, "boot_id": boot_id, "proc": proc, "name": name}
    proc.terminate()
    proc.wait(timeout=10)
    # The test stopped host 2 to simulate a failure; bring it back once to remove what it still owns.
    from app.provisioning.client import ProvisionerClient

    leftovers = [m.stem for m in (state_dir / "instances").glob("*.json")]
    if leftovers:
        cleanup = subprocess.Popen(
            [str(ROOT / "apps/provisioner/.venv/bin/uvicorn"), "dmx_provisioner.main:app", "--port", str(port)],
            cwd=ROOT / "apps/provisioner", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )  # fmt: skip
        try:
            client = ProvisionerClient(base_url=endpoint, token=token, timeout=120)
            for _ in range(100):
                try:
                    client.health()
                    break
                except Exception:
                    time.sleep(0.1)
            for iid in leftovers:
                client.destroy(iid)
        finally:
            cleanup.terminate()
            cleanup.wait(timeout=10)
    with system_session() as db:
        db.execute(update(InfrastructureHost).where(InfrastructureHost.id == host_id).values(status="retired"))
        db.execute(update(InfrastructureHost).where(InfrastructureHost.id == boot_id).values(status="active"))


def _set_status(admin, host_id, action):
    r = admin.post(f"/admin/hosts/{host_id}/status", json={"action": action, "reason": "integration test"})
    assert r.status_code == 200, r.text


def _url(user, database_id):
    return user.get(f"/databases/{database_id}/connection", params={"reveal": "true"}).json()["url"]


def _count(url, port):
    with _connect(url, port) as c:
        return c.execute("SELECT count(*) FROM items").fetchone()[0]


def test_multi_host_lifecycle(second_host, data_plane, make_user):
    port = data_plane["gateway_port"]
    h2, h1 = second_host["id"], second_host["boot_id"]
    admin = _make_admin(make_user)
    user = make_user()
    set_plan(user.user["id"], "pro")
    project = user.post("/projects", json={"name": "multi"}).json()

    listed = {h["name"]: h for h in admin.get("/admin/hosts").json()["items"]}
    assert listed[second_host["name"]]["heartbeat_fresh"] and listed[second_host["name"]]["token_registered"]

    # 1. Drained host 1 → new database lands on host 2 (published port on 127.0.0.1).
    _set_status(admin, h1, "drain")
    alpha = user.post("/databases", json={"project_id": project["id"], "name": "alpha"}).json()
    assert run_jobs() == ["succeeded"]
    with system_session() as db:
        d = db.get(Database, uuid.UUID(alpha["id"]))
        assert (
            d.host_id == h2 and d.internal_endpoint_host == "127.0.0.1" and 26000 <= d.internal_endpoint_port <= 26099
        )
    url = _url(user, alpha["id"])
    with _connect(url, port) as c:
        c.execute("CREATE TABLE items (id serial PRIMARY KEY, v text)")
        c.execute("INSERT INTO items (v) SELECT md5(g::text) FROM generate_series(1, 300) g")

    # 2. Backup on host 2, clone onto host 1 (encrypted object relayed between hosts).
    bk = user.post(f"/databases/{alpha['id']}/backups").json()
    assert run_jobs() == ["succeeded"]
    _set_status(admin, h1, "undrain")
    _set_status(admin, h2, "drain")
    r = user.post(f"/databases/{alpha['id']}/clone", json={"name": "alpha-copy", "backup_id": bk["id"]})
    assert r.status_code == 202, r.text
    copy = r.json()
    assert run_jobs() == ["succeeded"]
    with system_session() as db:
        assert db.get(Database, uuid.UUID(copy["id"])).host_id == h1
    assert _count(_url(user, copy["id"]), port) == 300
    boot_backups = os.environ.get("DMX_TEST_BOOT_BACKUP_DIR", "/var/lib/databasemax/customer-backups")
    relayed = Path(boot_backups) / alpha["id"] / f"{bk['id']}.dmxb"
    assert not relayed.exists()  # the temporary replica on host 1 was removed after the restore

    # 3. Explicit move host 2 → host 1: same URL and password, data intact, old instance removed.
    with _connect(url, port) as c:
        c.execute("INSERT INTO items (v) VALUES ('after backup')")
    with system_session() as db:
        old_iid = db.get(Database, uuid.UUID(alpha["id"])).internal_database_name
    r = admin.post(f"/admin/databases/{alpha['id']}/move", json={"target_host_id": str(h1), "reason": "rebalance"})
    assert r.status_code == 200, r.text
    assert user.get(f"/databases/{alpha['id']}").json()["status"] == "migrating"
    time.sleep(0.6)  # gateway route cache
    with pytest.raises(psycopg.OperationalError, match="being moved"):
        _connect(url, port)
    assert run_jobs() == ["succeeded"]
    moved = user.get(f"/databases/{alpha['id']}").json()
    assert moved["status"] == "ready" and moved["host"] == alpha["host"]
    with system_session() as db:
        d = db.get(Database, uuid.UUID(alpha["id"]))
        assert d.host_id == h1 and d.internal_database_name != old_iid
    assert _url(user, alpha["id"]) == url  # unchanged connection URL, same password
    assert _count(url, port) == 301
    with _connect(url, port) as c:
        c.execute("INSERT INTO items (v) VALUES ('writable again')")
    assert not _unit_exists(old_iid)

    # 4. Host failure: visible, alerted, and nothing is reassigned.
    _set_status(admin, h2, "undrain")
    _set_status(admin, h1, "drain")
    beta = user.post("/databases", json={"project_id": project["id"], "name": "beta"}).json()
    assert run_jobs() == ["succeeded"]
    beta_url = _url(user, beta["id"])
    _set_status(admin, h1, "undrain")
    second_host["proc"].terminate()
    second_host["proc"].wait(timeout=10)
    with system_session() as db:
        db.execute(
            update(InfrastructureHost)
            .where(InfrastructureHost.id == h2)
            .values(last_heartbeat_at=utcnow() - dt.timedelta(minutes=5))
        )
    with system_session() as db:
        stats = update_host_inventory(db, ProvisionerPool())
    assert stats["went_offline"] == 1
    infra = admin.get("/admin/infrastructure").json()
    assert any("test-host-2 is offline" in a["message"] and "not moved" in a["message"] for a in infra["alerts"])
    assert user.get(f"/databases/{beta['id']}").json()["host_status"] == "unavailable"
    time.sleep(0.6)  # gateway route cache
    with pytest.raises(psycopg.OperationalError, match="unavailable"):
        _connect(beta_url, port)
    with system_session() as db:
        stats = reconcile(db, ProvisionerPool())
        d = db.get(Database, uuid.UUID(beta["id"]))
        assert d.host_id == h2 and d.status == "ready"  # not re-created elsewhere
        assert (
            db.scalar(
                select(ProvisioningJob.id).where(
                    ProvisioningJob.database_id == d.id, ProvisioningJob.status.in_(("queued", "running"))
                )
            )
            is None
        )
    # New databases go to the healthy host meanwhile.
    gamma = user.post("/databases", json={"project_id": project["id"], "name": "gamma"}).json()
    assert run_jobs() == ["succeeded"]
    with system_session() as db:
        assert db.get(Database, uuid.UUID(gamma["id"])).host_id == h1
    for d in (alpha, copy, gamma):
        user.delete(f"/databases/{d['id']}", params={"confirm": d["name"]})
    run_jobs()

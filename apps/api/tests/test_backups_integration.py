"""End-to-end backups and clones against the real data plane (provisioner, host agent, gateway).
Skipped only when the provisioner isn't running (see conftest.data_plane)."""

from __future__ import annotations

import subprocess
import uuid

import psycopg
import pytest
from sqlalchemy import update

from app.core.db import system_session
from app.models import Backup
from app.services import backup_service
from tests.conftest import run_jobs
from tests.test_backups import set_plan
from tests.test_provisioning_integration import _connect, _create_ready, _gone, _instance

pytestmark = pytest.mark.integration


def _url(user, database_id: str) -> str:
    r = user.get(f"/databases/{database_id}/connection", params={"reveal": "true"})
    assert r.status_code == 200, r.text
    return r.json()["url"]


def _snapshot(c: psycopg.Connection) -> tuple:
    return c.execute(
        "SELECT (SELECT count(*) FROM orders), (SELECT sum(total) FROM orders), "
        "(SELECT md5(string_agg(sku, ',' ORDER BY id)) FROM orders)"
    ).fetchone()


def test_backup_clone_verify_and_final_backup(data_plane, user, project):
    port = data_plane["gateway_port"]
    set_plan(user.user["id"], "developer")
    src = _create_ready(user, project, "shop")
    src_url = _url(user, src["id"])
    with _connect(src_url, port) as c:
        c.execute("CREATE TABLE orders (id bigserial PRIMARY KEY, sku text NOT NULL, total numeric(10,2))")
        c.execute("CREATE INDEX orders_sku ON orders(sku)")
        c.execute("INSERT INTO orders (sku, total) SELECT 'sku-' || g, g * 1.5 FROM generate_series(1, 2000) g")
        at_backup = _snapshot(c)

    # 1. On-demand backup.
    r = user.post(f"/databases/{src['id']}/backups")
    assert r.status_code == 202, r.text
    bk = r.json()
    assert run_jobs() == ["succeeded"]
    bk = user.get(f"/backups/{bk['id']}").json()
    assert bk["status"] == "completed" and bk["restorable"] and bk["table_count"] == 1
    assert bk["size_bytes"] > 0 and bk["expires_at"]

    # Data written after the backup must not appear in a restore of that backup.
    with _connect(src_url, port) as c:
        c.execute("INSERT INTO orders (sku, total) VALUES ('after-backup', 1)")
        current = _snapshot(c)

    # 2. Restore the backup into a new database.
    r = user.post(f"/databases/{src['id']}/clone", json={"name": "shop-restored", "backup_id": bk["id"]})
    assert r.status_code == 202, r.text
    clone = r.json()
    assert run_jobs() == ["succeeded"]
    clone = user.get(f"/databases/{clone['id']}").json()
    assert clone["status"] == "ready" and clone["restored_from_backup_id"] == bk["id"]
    assert clone["data_restored_at"] and clone["host"] != src["host"]
    clone_url = _url(user, clone["id"])
    assert clone_url != src_url
    with _connect(clone_url, port) as c:
        assert _snapshot(c) == at_backup
        c.execute("INSERT INTO orders (sku, total) VALUES ('clone-write', 2)")  # writable, sequences restored
    # Independent credentials: the source's credentials don't open the clone's database.
    src_user = src_url.split("://")[1].split(":")[0]
    swapped = clone_url.replace(clone_url.split("://")[1].split(":")[0], src_user, 1)
    with pytest.raises(psycopg.OperationalError):
        _connect(swapped, port)
    # The source is untouched.
    with _connect(src_url, port) as c:
        assert _snapshot(c) == current

    # 3. Copy current data (takes a fresh pre-clone backup of the source first).
    r = user.post(f"/databases/{src['id']}/clone", json={"name": "shop-copy", "environment": "staging"})
    assert r.status_code == 202, r.text
    copy = r.json()
    assert run_jobs() == ["succeeded"]
    copy = user.get(f"/databases/{copy['id']}").json()
    assert copy["status"] == "ready" and copy["environment"] == "staging"
    with _connect(_url(user, copy["id"]), port) as c:
        assert _snapshot(c) == current
    kinds = {b["kind"] for b in user.get(f"/databases/{src['id']}/backups").json()["items"]}
    assert kinds == {"manual", "pre_clone"}

    # 4. Restore verification in a scratch instance.
    with system_session() as db:
        from app.models import Database

        backup_service.request_verification(
            db, db.get(Database, uuid.UUID(src["id"])), db.get(Backup, uuid.UUID(bk["id"]))
        )
    assert run_jobs() == ["succeeded"]
    v = user.get(f"/backups/{bk['id']}").json()
    assert v["verification_status"] == "passed" and v["status"] == "verified" and v["verified_at"]
    assert not any(line.startswith("dmx-pg@vfy") for line in subprocess.run(
        ["systemctl", "list-units", "--all", "--plain", "--no-legend", "dmx-pg@*"], capture_output=True, text=True
    ).stdout.splitlines())  # fmt: skip

    # 5. Delete with a final backup, then restore from it after the source is gone.
    iid = _instance(src["id"])
    r = user.delete(f"/databases/{src['id']}", params={"confirm": "shop", "final_backup": "true"})
    assert r.status_code == 202, r.text
    assert run_jobs() == ["succeeded"]
    assert _gone(iid)
    finals = [b for b in user.get("/backups").json()["items"] if b["kind"] == "final"]
    assert len(finals) == 1 and finals[0]["database_status"] == "deleted" and finals[0]["restorable"]
    r = user.post(f"/databases/{src['id']}/clone", json={"name": "shop-reborn", "backup_id": finals[0]["id"]})
    assert r.status_code == 202, r.text
    assert run_jobs() == ["succeeded"]
    reborn = r.json()
    with _connect(_url(user, reborn["id"]), port) as c:
        assert _snapshot(c) == current

    for d in (clone, copy, reborn):
        user.delete(f"/databases/{d['id']}", params={"confirm": d["name"]})
    assert set(run_jobs()) == {"succeeded"}


def test_corrupted_backup_fails_clone_and_cleans_up(data_plane, user, project):
    set_plan(user.user["id"], "developer")
    src = _create_ready(user, project, "victim")
    r = user.post(f"/databases/{src['id']}/backups")
    assert run_jobs() == ["succeeded"]
    bk = r.json()
    # The control plane's recorded checksum no longer matches the stored object.
    with system_session() as db:
        db.execute(update(Backup).where(Backup.id == uuid.UUID(bk["id"])).values(checksum_sha256="0" * 64))
    r = user.post(f"/databases/{src['id']}/clone", json={"name": "victim-copy", "backup_id": bk["id"]})
    assert r.status_code == 202
    target = r.json()
    iid = _instance(target["id"])
    assert run_jobs() == ["failed"]
    t = user.get(f"/databases/{target['id']}").json()
    assert t["status"] == "failed" and t["provisioning"]["error_code"] == "backup_integrity"
    assert _gone(iid)
    user.delete(f"/databases/{src['id']}", params={"confirm": "victim"})
    run_jobs()

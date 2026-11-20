"""Hard storage limits end to end: a customer instance on loop-backed tablespace, WAL and PGDATA
filesystems (host agent) cannot exceed its allocation, stays up when full, and can be grown.
Needs root, loop devices and a host prepared by host-setup.sh (DMX_HOST_TESTS=1)."""

import os
import secrets
import subprocess
import uuid
from collections.abc import Iterator

import psycopg
import pytest

from tests.conftest import needs_host

pytestmark = needs_host


BATCH = "INSERT INTO blob (payload) SELECT repeat(md5(g::text), 60) FROM generate_series(1, 4000) g"


@pytest.fixture(scope="module")
def loop_backend() -> Iterator[None]:
    os.environ["DMX_TEST_STORAGE_BACKEND"] = "loop"
    yield
    os.environ.pop("DMX_TEST_STORAGE_BACKEND", None)


@pytest.fixture(scope="module")
def loop_host(loop_backend, host) -> Iterator[str]:  # type: ignore[no-untyped-def]
    assert host["mode"] == "loop"
    yield host["storage_root"]


def test_full_database_is_contained_and_growable(loop_host) -> None:  # type: ignore[no-untyped-def]
    from dmx_provisioner import host_agent, pgadmin
    from dmx_provisioner import instances as backend
    from dmx_provisioner.scram import verifier

    iid = "q" + secrets.token_hex(8)
    pw = secrets.token_urlsafe(24)
    role = "u_q" + secrets.token_hex(3)
    try:
        info = backend.ensure_instance(iid, version="16", profile_name="nano", storage_mb=128, max_connections=5,
                                       database_id=str(uuid.uuid4()), owner_id=str(uuid.uuid4()))  # fmt: skip
        for name in (iid, f"{iid}-wal", f"{iid}-sys"):
            assert host_agent.get_volume(name)["mounted"]
        assert info.tablespace == "dmx_data"
        pgadmin.configure(iid, info.mgmt_host, database="app", username=role, scram_verifier=verifier(pw),
                          connection_limit=5, tablespace=info.tablespace)  # fmt: skip

        def conn() -> psycopg.Connection:  # type: ignore[type-arg]
            return psycopg.connect(host=info.endpoint_host, port=info.endpoint_port, dbname="app", user=role,
                                   password=pw, autocommit=True, connect_timeout=5, sslmode="disable")  # fmt: skip

        with conn() as c:
            c.execute("CREATE TABLE blob (id serial, payload text)")
            c.execute("ALTER TABLE blob ALTER COLUMN payload SET STORAGE PLAIN")
            full = None
            for _ in range(80):  # ~8 MB per batch, well past the 128 MB allocation
                try:
                    c.execute(BATCH)
                except psycopg.errors.DiskFull as e:
                    full = e
                    break
            assert full is not None, "the hard limit was never reached"
            assert "No space left on device" in str(full)
        vol = host_agent.get_volume(iid)
        assert vol["fs_used_bytes"] <= vol["fs_size_bytes"] <= 220 * 2**20  # bounded by the volume, not the host
        # The server survived: WAL and PGDATA have their own filesystems, so a full tablespace
        # can't break checkpoints or WAL writes.
        with conn() as c:
            ts = c.execute("SELECT spcname FROM pg_tablespace t JOIN pg_database d ON d.dattablespace = t.oid "
                           "WHERE d.datname = current_database()").fetchone()[0]  # fmt: skip
            assert ts == "dmx_data"
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute("CREATE TABLE escape (x int) TABLESPACE pg_default")
            n = c.execute("SELECT count(*) FROM blob").fetchone()[0]
            assert n > 0
            assert c.execute("SELECT pg_postmaster_start_time() < now() - interval '1 second'").fetchone()[0]

        # Growing the allocation makes room again (online when the host allows, else stop/grow/start).
        r = backend.resize_storage(iid, 512)
        assert r["changed"] is True and r["fs_size_bytes"] > 500 * 2**20
        with conn() as c:
            c.execute(BATCH)
            assert c.execute("SELECT count(*) FROM blob").fetchone()[0] == n + 4000
        # Shrinking keeps the larger filesystem (soft limit applies at the control plane).
        assert backend.resize_storage(iid, 256)["changed"] is False
    finally:
        if os.environ.get("DMX_TEST_DEBUG"):
            print(subprocess.run(["journalctl", "-u", f"dmx-pg@{iid}", "-n", "60", "--no-pager"],
                                 capture_output=True, text=True, check=False).stdout)  # fmt: skip
        backend.destroy_instance(iid)
    assert all(host_agent.get_volume(n) is None for n in (iid, f"{iid}-wal", f"{iid}-sys"))
    assert not any(f.startswith(iid) for f in os.listdir(os.path.join(loop_host, "images")))

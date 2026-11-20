"""End-to-end backup/restore/verify against real customer instances (systemd units on a host
prepared by host-setup.sh; run as root with DMX_HOST_TESTS=1 — see tests/conftest.py)."""

import os
import secrets
import uuid
from collections.abc import Iterator

import psycopg
import pytest

from tests.conftest import needs_host

pytestmark = needs_host


@pytest.fixture(scope="module")
def env(host) -> Iterator[None]:  # type: ignore[no-untyped-def]
    yield


def _iid() -> str:
    return "t" + secrets.token_hex(8)


@pytest.fixture(scope="module")
def instances(env) -> Iterator[list[str]]:  # type: ignore[no-untyped-def]
    from dmx_provisioner import instances as backend

    created: list[str] = []
    yield created
    for iid in created:
        backend.destroy_instance(iid)


def _make(instances: list[str], database: str, user: str, password: str) -> tuple[str, tuple[str, int]]:
    from dmx_provisioner import instances as backend
    from dmx_provisioner import pgadmin
    from dmx_provisioner.scram import verifier

    iid = _iid()
    instances.append(iid)
    info = backend.ensure_instance(
        iid, version="16", profile_name="nano", storage_mb=512, max_connections=10,
        database_id=str(uuid.uuid4()), owner_id=str(uuid.uuid4()),
    )  # fmt: skip
    pgadmin.configure(iid, info.mgmt_host, database=database, username=user,
                      scram_verifier=verifier(password), connection_limit=10)  # fmt: skip
    assert info.endpoint_host is not None
    return iid, (info.endpoint_host, info.endpoint_port)  # where the gateway would connect


def _conn(addr: tuple[str, int], db: str, user: str, pw: str) -> psycopg.Connection:  # type: ignore[type-arg]
    return psycopg.connect(host=addr[0], port=addr[1], dbname=db, user=user, password=pw, autocommit=True,
                           connect_timeout=5, sslmode="disable")  # fmt: skip


SEED = """
CREATE TABLE customers (id bigserial PRIMARY KEY, email text UNIQUE NOT NULL, created_at timestamptz DEFAULT now());
CREATE TABLE orders (id bigserial PRIMARY KEY, customer_id bigint REFERENCES customers(id), total numeric(10,2));
CREATE INDEX orders_customer_idx ON orders(customer_id);
CREATE VIEW big_orders AS SELECT * FROM orders WHERE total > 100;
CREATE FUNCTION order_count() RETURNS bigint LANGUAGE sql AS 'SELECT count(*) FROM orders';
CREATE SCHEMA analytics;
CREATE TABLE analytics.events (id int, payload jsonb) PARTITION BY RANGE (id);
CREATE TABLE analytics.events_p1 PARTITION OF analytics.events FOR VALUES FROM (0) TO (1000);
INSERT INTO customers (email) SELECT 'user' || g || '@example.com' FROM generate_series(1, 500) g;
INSERT INTO orders (customer_id, total) SELECT (g % 500) + 1, (g * 7 % 300)::numeric FROM generate_series(1, 5000) g;
INSERT INTO analytics.events SELECT g, jsonb_build_object('n', g) FROM generate_series(0, 999) g;
"""

SNAPSHOT = """
SELECT (SELECT count(*) FROM customers), (SELECT count(*) FROM orders), (SELECT sum(total) FROM orders),
       (SELECT count(*) FROM big_orders), order_count(), (SELECT count(*) FROM analytics.events),
       (SELECT md5(string_agg(email, ',' ORDER BY id)) FROM customers)
"""


def test_backup_restore_verify_roundtrip(instances) -> None:  # type: ignore[no-untyped-def]
    from dmx_provisioner import backups
    from dmx_provisioner import instances as backend
    from dmx_provisioner.backup_store import get_store, object_key

    src_pw = secrets.token_urlsafe(24)
    src_role = "u_src" + secrets.token_hex(3)
    src, src_host = _make(instances, "shop", src_role, src_pw)
    with _conn(src_host, "shop", src_role, src_pw) as c:
        c.execute(SEED)
        expected = c.execute(SNAPSHOT).fetchone()

    db_id, backup_id = backend.get_instance(src).database_id, str(uuid.uuid4())  # type: ignore[union-attr]
    r = backups.create_backup(src, database="shop", database_id=db_id, backup_id=backup_id)
    assert r.table_count == 4  # customers, orders, analytics.events (+ partition)
    assert r.size > 0 and len(r.sha256) == 64 and r.key_id == "dev"
    with get_store().open(object_key(db_id, backup_id)) as f:
        raw = f.read()
    assert raw.startswith(b"DMXB2") and b"user1@example.com" not in raw and b"PGDMP" not in raw

    # Restore into a brand-new instance owned by a different role with a different password.
    dst_pw = secrets.token_urlsafe(24)
    dst_role = "u_dst" + secrets.token_hex(3)
    dst, dst_host = _make(instances, "shop_copy", dst_role, dst_pw)
    out = backups.restore_backup(dst, database="shop_copy", username=dst_role,
                                 source_database_id=db_id, backup_id=backup_id, sha256=r.sha256)  # fmt: skip
    assert out["table_count"] == r.table_count
    with _conn(dst_host, "shop_copy", dst_role, dst_pw) as c:
        assert c.execute(SNAPSHOT).fetchone() == expected
        owners = {row[0] for row in c.execute(
            "SELECT tableowner FROM pg_tables WHERE schemaname IN ('public','analytics')")}  # fmt: skip
        assert owners == {dst_role}
        c.execute("INSERT INTO customers (email) VALUES ('new@example.com')")  # sequences restored and usable
    # The source credentials do not exist on the clone.
    with pytest.raises(psycopg.OperationalError):
        _conn(dst_host, "shop_copy", src_role, src_pw)

    # Restores only ever go into an empty database.
    with pytest.raises(backups.BackupFailed) as ei:
        backups.restore_backup(dst, database="shop_copy", username=dst_role,
                               source_database_id=db_id, backup_id=backup_id, sha256=r.sha256)  # fmt: skip
    assert ei.value.code == "target_not_empty"

    # Verification restores into a scratch instance and removes it afterwards.
    before = {i.instance_id for i in backend.list_instances()}
    v = backups.verify_backup(source_database_id=db_id, backup_id=backup_id, sha256=r.sha256,
                              pg_version="16", expected_tables=r.table_count)  # fmt: skip
    assert v["verified"] is True and v["table_count"] == r.table_count
    assert {i.instance_id for i in backend.list_instances()} == before

    # Tampering is detected before anything reaches pg_restore.
    path = os.path.join(os.environ["DMX_BACKUP_DIR"], object_key(db_id, backup_id))
    with open(path, "r+b") as f:
        blob = bytearray(f.read())
        blob[len(blob) // 2] ^= 0x01
        f.seek(0)
        f.write(bytes(blob))
    t_pw = secrets.token_urlsafe(24)
    t_role = "u_tmp" + secrets.token_hex(3)
    tgt, tgt_host = _make(instances, "tampered", t_role, t_pw)
    with pytest.raises(backups.BackupFailed) as ei:
        backups.restore_backup(tgt, database="tampered", username=t_role,
                               source_database_id=db_id, backup_id=backup_id, sha256=r.sha256)  # fmt: skip
    assert ei.value.code == "integrity_error"
    with _conn(tgt_host, "tampered", t_role, t_pw) as c:
        assert c.execute("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'").fetchone()[0] == 0

    assert backups.delete_backup(db_id, backup_id) is True
    assert backups.delete_backup(db_id, backup_id) is False


def test_backup_of_missing_database_fails_cleanly(instances) -> None:  # type: ignore[no-untyped-def]
    from dmx_provisioner import backups
    from dmx_provisioner import instances as backend
    from dmx_provisioner.backup_store import get_store, object_key

    iid, _ = _make(instances, "real", "u_x" + secrets.token_hex(3), secrets.token_urlsafe(24))
    db_id, backup_id = backend.get_instance(iid).database_id, str(uuid.uuid4())  # type: ignore[union-attr]
    with pytest.raises(Exception):  # noqa: B017 - count_tables or pg_dump fails; no object is left behind
        backups.create_backup(iid, database="nosuchdb", database_id=db_id, backup_id=backup_id)
    assert not get_store().exists(object_key(db_id, backup_id))


def test_backup_refuses_mismatched_database_id(instances) -> None:  # type: ignore[no-untyped-def]
    from dmx_provisioner import backups

    iid, _ = _make(instances, "mine", "u_m" + secrets.token_hex(3), secrets.token_urlsafe(24))
    with pytest.raises(backups.BackupFailed) as ei:
        backups.create_backup(iid, database="mine", database_id=str(uuid.uuid4()), backup_id=str(uuid.uuid4()))
    assert ei.value.code == "not_found"

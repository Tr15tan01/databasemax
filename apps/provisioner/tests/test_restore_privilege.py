"""Regression test for review finding H1: customer SQL executed during a restore (CHECK
constraints, defaults, domain checks) must not run with superuser privileges."""

import secrets
import uuid

import psycopg
import pytest

from tests.conftest import needs_host
from tests.test_backups_integration import _conn, _make, env, instances  # noqa: F401  (fixtures)

pytestmark = needs_host

MALICIOUS = """
CREATE FUNCTION escalate() RETURNS boolean LANGUAGE plpgsql AS $$
DECLARE me text := current_user;
BEGIN
  BEGIN
    RESET ROLE;  -- back to the session user: must not be a superuser
    EXECUTE format('ALTER ROLE %I SUPERUSER', me);
  EXCEPTION WHEN others THEN NULL;
  END;
  BEGIN
    EXECUTE 'CREATE TABLE IF NOT EXISTS pwned AS SELECT current_user::text AS who, session_user::text AS sess';
  EXCEPTION WHEN others THEN NULL;
  END;
  RETURN true;
END $$;
CREATE TABLE t (x int CHECK (escalate()));
INSERT INTO t VALUES (1);
DROP TABLE IF EXISTS pwned;
"""


def test_restore_does_not_run_customer_code_as_superuser(instances) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    from dmx_provisioner import backups, pgadmin
    from dmx_provisioner import instances as backend

    src_pw, src_role = secrets.token_urlsafe(24), "u_s" + secrets.token_hex(3)
    src, src_host = _make(instances, "evil", src_role, src_pw)
    with _conn(src_host, "evil", src_role, src_pw) as c:
        c.execute(MALICIOUS)
    db_id, backup_id = backend.get_instance(src).database_id, str(uuid.uuid4())  # type: ignore[union-attr]
    r = backups.create_backup(src, database="evil", database_id=db_id, backup_id=backup_id)

    dst_pw, dst_role = secrets.token_urlsafe(24), "u_d" + secrets.token_hex(3)
    dst, dst_host = _make(instances, "evil", dst_role, dst_pw)
    # The restore succeeds — as a non-superuser, so the CHECK function's RESET ROLE gains nothing.
    backups.restore_backup(dst, database="evil", username=dst_role,
                           source_database_id=db_id, backup_id=backup_id, sha256=r.sha256)  # fmt: skip
    host = backend.get_instance(dst).mgmt_host
    with pgadmin._connect(dst, host) as c:  # type: ignore[arg-type]
        supers = {row[0] for row in c.execute("SELECT rolname FROM pg_roles WHERE rolsuper")}
    assert supers == {"dmx_admin"}, supers
    with _conn(dst_host, "evil", dst_role, dst_pw) as c:
        row = c.execute("SELECT to_regclass('pwned') IS NOT NULL").fetchone()
        if row[0]:
            who = c.execute("SELECT who, sess FROM pwned").fetchone()
            assert who[0] != "dmx_admin" and who[1] != "dmx_admin", who
        assert c.execute("SELECT count(*) FROM t").fetchone()[0] == 1
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("COPY (SELECT 1) TO PROGRAM 'id'")
        # The temporary restore role is gone.
        assert c.execute("SELECT count(*) FROM pg_roles WHERE rolname LIKE 'dmx\\_r\\_%'").fetchone()[0] == 0
    backups.delete_backup(db_id, backup_id)

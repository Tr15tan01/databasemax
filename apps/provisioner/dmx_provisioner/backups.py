"""Backup, restore and restore-verification for customer instances.

- Backups are logical (`pg_dump -Fc`), run by the provisioner with the client binaries of the
  instance's own PostgreSQL version, over loopback as the instance's admin role, with a fixed argv
  (no shell; identifiers validated first) and a minimal environment.
- The dump is encrypted as it streams (see backup_crypto) and stored via backup_store. Plaintext
  customer data never touches disk and never passes through the control plane.
- Restores always target a freshly created, empty database; before any byte reaches
  pg_restore, the whole object is authenticated and its checksum compared with the one the
  control plane recorded, so a tampered or truncated object is rejected up front.
- Verification restores a backup into a scratch instance (no gateway network, never reachable
  by customers), compares the table count with the one recorded at backup time, and destroys
  the scratch instance.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import re
import secrets
import shutil
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import IO, Any

from dmx_provisioner import backup_crypto as crypto
from dmx_provisioner import instances as backend
from dmx_provisioner import pgadmin
from dmx_provisioner.backup_store import get_store, object_key
from dmx_provisioner.config import backup_keyring, get_settings
from dmx_provisioner.scram import verifier
from dmx_provisioner.validation import ValidationFailed
from dmx_provisioner.validation import db_name as check_db
from dmx_provisioner.validation import role_name as check_role

log = logging.getLogger("dmx.provisioner.backups")

SNAPSHOT_RE = re.compile(r"^[0-9A-F]{8}-[0-9A-F]{8}-[0-9]+$")


class BackupFailed(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class BackupResult:
    storage_reference: str
    size: int
    sha256: str
    key_id: str
    table_count: int
    duration_ms: int
    store: str


def _deadline(seconds: int) -> list[str]:
    # coreutils `timeout`: a stuck dump/restore is terminated (then killed), so it can never pin
    # a provisioner thread or an instance indefinitely.
    return ["timeout", "--signal=TERM", "--kill-after=30", str(int(seconds))]


def _target(iid: str) -> tuple[str, list[str]]:
    m = backend.load_meta(iid)
    if m is None:
        raise BackupFailed("not_found", "instance not found")
    return m.pg_version, ["--host", "127.0.0.1", "--port", str(m.port)]


def dump_argv(iid: str, database: str, snapshot: str | None = None) -> list[str]:
    check_db(database)
    version, target = _target(iid)
    extra = []
    if snapshot is not None:
        if not SNAPSHOT_RE.match(snapshot):
            raise ValidationFailed("invalid snapshot id")
        extra = ["--snapshot", snapshot]
    return [
        *_deadline(get_settings().backup_timeout_seconds),
        backend.pg_bin(version, "pg_dump"),
        *target,
        "--username", "dmx_admin",
        "--dbname", database,
        "--format", "custom",
        "--compress", "6",
        "--no-owner",
        "--no-acl",
        "--no-tablespaces",
        "--no-password",
        # Don't queue behind a tenant's long-held exclusive lock.
        "--lock-wait-timeout", "60s",
        *extra,
    ]  # fmt: skip


def restore_argv(iid: str, database: str, role: str, login_role: str) -> list[str]:
    check_db(database)
    version, target = _target(iid)
    check_role(role)
    if not login_role.startswith("dmx_r_"):
        raise ValidationFailed("restores must use a restore role")
    return [
        *_deadline(get_settings().backup_timeout_seconds),
        backend.pg_bin(version, "pg_restore"),
        *target,
        "--username", login_role,  # non-superuser; see pgadmin.create_restore_role
        "--dbname", database,
        "--role", role,  # objects are created as (and owned by) the customer role
        "--no-owner",
        "--no-acl",
        "--no-tablespaces",
        "--exit-on-error",
        "--single-transaction",
        "--no-password",
    ]  # fmt: skip


def _mgmt_host(iid: str, database_id: str | None = None) -> str:
    i = backend.get_instance(iid)
    if i is None:
        raise BackupFailed("not_found", "instance not found")
    if database_id is not None and i.database_id != database_id:
        # Defence in depth: the instance must belong to the database the caller names.
        raise BackupFailed("not_found", "instance does not belong to this database")
    if i.state != "running" or not i.mgmt_host:
        raise BackupFailed("not_running", "instance is not running")
    return i.mgmt_host


def _context(database_id: str, backup_id: str) -> bytes:
    """Bound into every chunk's AAD: an object can't be swapped for another backup."""
    return f"{database_id}/{backup_id}".encode()


def create_backup(iid: str, *, database: str, database_id: str, backup_id: str) -> BackupResult:
    s = get_settings()
    key = object_key(database_id, backup_id)
    keys = backup_keyring(s)
    if s.backup_active_key not in keys:
        raise BackupFailed("misconfigured", "backup encryption key is not configured")
    store = get_store()
    host = _mgmt_host(iid, database_id)
    if store.name == "local":
        # Reserve room for this dump (upper bound: the database's size) within the budget and
        # on the filesystem, so concurrent dumps can't overshoot either.
        estimate = pgadmin.database_size(iid, host, database=database)
        used = store.usage_bytes()
        if used + estimate > s.backup_disk_budget_mb * 2**20:
            raise BackupFailed("capacity_exceeded", "backup storage budget exhausted")
        free = getattr(store, "free_bytes", lambda: None)()
        if free is not None and free < estimate + 512 * 2**20:
            raise BackupFailed("capacity_exceeded", "not enough free disk for this backup")
    t0 = time.monotonic()
    # Table count and dump come from the same exported snapshot, so verification compares like
    # with like even if the tenant is changing its schema meanwhile.
    with pgadmin.snapshot(iid, host, database=database) as (snap, tables):
        plain = backend.exec_read(dump_argv(iid, database, snap), environment=backend.admin_env(iid))
        try:
            obj = store.put(
                key,
                crypto.encrypt_stream(
                    plain, keys[s.backup_active_key], s.backup_active_key, context=_context(database_id, backup_id)
                ),
            )
        except backend.ExecFailed as e:
            msg = f"pg_dump failed: {e.detail}" if e.detail else "pg_dump failed"
            raise BackupFailed("dump_failed", msg) from e
    return BackupResult(
        storage_reference=obj.reference,
        size=obj.size,
        sha256=obj.sha256,
        key_id=s.backup_active_key,
        table_count=tables,
        duration_ms=int((time.monotonic() - t0) * 1000),
        store=store.name,
    )


def _open(key: str) -> IO[bytes]:
    try:
        return get_store().open(key)
    except FileNotFoundError as e:
        raise BackupFailed("backup_missing", "backup object not found") from e


@contextlib.contextmanager
def verified_object(database_id: str, backup_id: str, expected_sha256: str) -> Iterator[IO[bytes]]:
    """Open the object ONCE, authenticate every chunk and check the recorded SHA-256, then hand
    back the same open file (rewound) for restoring — so what is restored is exactly what was
    verified. Remote objects are first copied (still encrypted) to a private spool file."""
    key = object_key(database_id, backup_id)
    keys = backup_keyring(get_settings())
    src = _open(key)
    spool: IO[bytes] | None = None
    try:
        if getattr(src, "seekable", lambda: False)():
            f = src
        else:
            spool = tempfile.TemporaryFile(dir=_spool_dir())  # noqa: SIM115 - closed in finally
            shutil.copyfileobj(src, spool, 1024 * 1024)
            f = spool
        f.seek(0)
        h = hashlib.sha256()

        def read(n: int) -> bytes:
            b = f.read(n)
            h.update(b)
            return b

        try:
            for _ in crypto.decrypt_stream(read, keys, context=_context(database_id, backup_id)):
                pass
        except crypto.BackupIntegrityError as e:
            raise BackupFailed("integrity_error", str(e)) from e
        if not expected_sha256 or not secrets.compare_digest(h.hexdigest(), expected_sha256.lower()):
            raise BackupFailed("integrity_error", "backup checksum does not match the recorded checksum")
        f.seek(0)
        yield f
    finally:
        src.close()
        if spool is not None:
            spool.close()


def _spool_dir() -> str:
    d = os.path.join(get_settings().backup_dir, ".spool")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def authenticate(database_id: str, backup_id: str, expected_sha256: str) -> None:
    with verified_object(database_id, backup_id, expected_sha256):
        pass


def _is_empty(iid: str, host: str, database: str) -> bool:
    return pgadmin.count_tables(iid, host, database=database) == 0


def restore_backup(
    iid: str, *, database: str, username: str, source_database_id: str, backup_id: str, sha256: str
) -> dict[str, Any]:
    """Restore into a freshly configured, empty database on instance `iid`."""
    check_db(database)
    check_role(username)
    if not sha256:
        raise ValidationFailed("checksum required")
    host = _mgmt_host(iid)
    if not _is_empty(iid, host, database):
        raise BackupFailed("target_not_empty", "restores only go into a new, empty database")
    keys = backup_keyring(get_settings())
    t0 = time.monotonic()
    with verified_object(source_database_id, backup_id, sha256) as f:
        password = secrets.token_urlsafe(32)
        login = pgadmin.create_restore_role(
            iid, host, database=database, owner_role=username, scram_verifier=verifier(password)
        )
        try:
            backend.exec_write(
                restore_argv(iid, database, username, login),
                crypto.decrypt_stream(f.read, keys, context=_context(source_database_id, backup_id)),
                timeout=get_settings().backup_timeout_seconds,
                environment={"PGPASSWORD": password, "PGSSLMODE": "disable", "PGCONNECT_TIMEOUT": "10"},
            )
        except crypto.BackupIntegrityError as e:
            raise BackupFailed("integrity_error", str(e)) from e
        except backend.ExecFailed as e:
            msg = f"pg_restore failed: {e.detail}" if e.detail else "pg_restore failed"
            raise BackupFailed("restore_failed", msg) from e
        finally:
            pgadmin.drop_restore_role(iid, host, database=database, owner_role=username, name=login)
    tables = pgadmin.count_tables(iid, host, database=database)
    return {"table_count": tables, "duration_ms": int((time.monotonic() - t0) * 1000)}


def verify_backup(
    *,
    source_database_id: str,
    backup_id: str,
    sha256: str,
    pg_version: str,
    expected_tables: int | None,
    storage_mb: int = 1024,
) -> dict[str, Any]:
    """Restore into a throwaway instance and check the result. Always destroys the scratch instance."""
    s = get_settings()
    backend.cleanup_stale_verify_instances()
    authenticate(source_database_id, backup_id, sha256)  # fail fast before creating an instance
    iid = "vfy" + secrets.token_hex(8)
    role = "verify_" + secrets.token_hex(4)
    t0 = time.monotonic()
    try:
        backend.ensure_instance(
            iid,
            version=pg_version,
            profile_name=s.verify_profile,
            storage_mb=storage_mb,
            max_connections=5,
            database_id=f"verify:{backup_id}",
            owner_id="system",
            purpose="verify",
        )
        host = _mgmt_host(iid)
        scratch = backend.get_instance(iid)
        pgadmin.configure(
            iid,
            host,
            tablespace=scratch.tablespace if scratch else None,
            database="verify",
            username=role,
            scram_verifier=verifier(secrets.token_urlsafe(32)),
            connection_limit=5,
        )
        result = restore_backup(
            iid,
            database="verify",
            username=role,
            source_database_id=source_database_id,
            backup_id=backup_id,
            sha256=sha256,
        )
    finally:
        try:
            backend.destroy_instance(iid)
        except Exception:  # the stale-instance sweep retries
            log.warning("failed to remove scratch verification instance")
    ok = expected_tables is None or result["table_count"] == expected_tables
    return {
        "verified": ok,
        "table_count": result["table_count"],
        "expected_table_count": expected_tables,
        "duration_ms": int((time.monotonic() - t0) * 1000),
    }


def delete_backup(database_id: str, backup_id: str) -> bool:
    return get_store().delete(object_key(database_id, backup_id))


def usage() -> dict[str, Any]:
    s = get_settings()
    store = get_store()
    out: dict[str, Any] = {"store": store.name, "active_key_id": s.backup_active_key}
    try:
        out["used_bytes"] = store.usage_bytes()
    except Exception:
        out["used_bytes"] = None
    if store.name == "local":
        out["budget_mb"] = s.backup_disk_budget_mb
    return out

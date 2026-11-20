"""Provisioning job execution.

Jobs are rows in `provisioning_jobs`, claimed with FOR UPDATE SKIP LOCKED and a lease. Every
handler is idempotent: it can be re-run after a crash at any point (the provisioner's
operations are idempotent, credentials are persisted before use, and state transitions are
checked). Progress is committed step by step so the UI shows the current stage.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import logging
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import new_session
from app.core.logging import log_event
from app.models import Backup, Database, DatabaseCredential, InfrastructureHost, ProvisioningJob, utcnow
from app.provisioning import state_machine as sm
from app.provisioning.client import ProvisionerClient, ProvisionerError
from app.provisioning.connectivity import ConnectivityError, verify
from app.provisioning.hosts import HostUnavailable, ProvisionerPool, is_fresh
from app.provisioning.placement import NoCapacity, choose_host
from app.security.scram import scram_sha256_verifier
from app.security.secrets_store import secret_store
from app.security.tokens import random_lower_alnum, random_password
from app.services.audit import audit
from app.services.notifications import notify

log = logging.getLogger("dmx.worker.jobs")


class JobFailed(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.retryable = retryable


# --- claiming ----------------------------------------------------------------------------------

CLAIM_SQL = text(
    """
    UPDATE provisioning_jobs SET
      status = 'running',
      lease_owner = :owner,
      lease_expires_at = now() + make_interval(secs => :lease),
      attempt_count = attempt_count + 1,
      started_at = coalesce(started_at, now())
    WHERE id = (
      SELECT id FROM provisioning_jobs
      WHERE (status = 'queued' AND run_after <= now())
         OR (status = 'running' AND lease_expires_at < now())
      ORDER BY run_after, created_at
      FOR UPDATE SKIP LOCKED
      LIMIT 1
    )
    RETURNING id
    """
)


def claim_next(worker_id: str) -> uuid.UUID | None:
    s = get_settings()
    db = new_session(system=True)
    try:
        row = db.execute(CLAIM_SQL, {"owner": worker_id, "lease": s.job_lease_seconds}).first()
        db.commit()
        return row[0] if row else None
    finally:
        db.close()


def extend_lease(db: Session, job: ProvisioningJob, worker_id: str, *, long: bool = False) -> None:
    s = get_settings()
    seconds = s.long_job_lease_seconds if long else s.job_lease_seconds
    job.lease_expires_at = utcnow() + dt.timedelta(seconds=seconds)
    job.lease_owner = worker_id


# --- helpers --------------------------------------------------------------------------------------


def _username(database: Database) -> str:
    # Globally unique (route ids are unique), so the gateway can route by username alone.
    return "u_" + database.route_id.removeprefix("db-")


def _pending_credential(db: Session, database: Database) -> DatabaseCredential:
    cred = db.scalar(
        select(DatabaseCredential)
        .where(
            DatabaseCredential.database_id == database.id,
            DatabaseCredential.activated_at.is_(None),
            DatabaseCredential.revoked_at.is_(None),
        )
        .order_by(DatabaseCredential.created_at.desc())
        .limit(1)
    )
    if cred is None:
        password = random_password(32)
        cred = DatabaseCredential(
            id=uuid.uuid4(),
            database_id=database.id,
            owner_id=database.owner_id,
            username=_username(database),
        )
        db.add(cred)
        db.flush()
        cred.secret_reference = secret_store.put(db, password, purpose="db_password")
    return cred


def _active_credential(db: Session, database: Database) -> DatabaseCredential | None:
    return db.scalar(
        select(DatabaseCredential).where(
            DatabaseCredential.database_id == database.id,
            DatabaseCredential.activated_at.is_not(None),
            DatabaseCredential.revoked_at.is_(None),
        )
    )


def _password(db: Session, cred: DatabaseCredential) -> str:
    assert cred.secret_reference is not None
    return secret_store.get(db, cred.secret_reference)


def _prov_call(fn: Callable[..., Any], *a: Any, **kw: Any) -> Any:
    try:
        return fn(*a, **kw)
    except ProvisionerError as e:
        if e.code == "capacity_exceeded":
            raise JobFailed("capacity_exceeded", "No capacity is available right now; we'll retry shortly.") from e
        if e.code == "integrity_error":
            raise JobFailed(
                "backup_integrity",
                "The backup failed its integrity check and can't be restored. Operators have been alerted.",
                retryable=False,
            ) from e
        if e.code == "backup_missing":
            raise JobFailed("backup_missing", "The backup file is no longer available.", retryable=False) from e
        if e.code == "not_running":
            raise JobFailed("not_running", "The database instance is not running.", retryable=False) from e
        if e.code == "dump_failed":
            raise JobFailed("backup_failed", "PostgreSQL could not produce a backup; retrying.") from e
        if e.code == "restore_failed":
            raise JobFailed(
                "restore_failed", "The backup could not be restored into the new database.", retryable=False
            ) from e
        if e.code == "provisioner_unreachable":
            raise JobFailed("provisioner_unreachable", "Infrastructure service unavailable; retrying.") from e
        if e.status == 422:
            raise JobFailed(
                "invalid_request", "The request was rejected by the infrastructure service.", retryable=False
            ) from e
        if e.status == 404:
            raise JobFailed(
                "instance_missing",
                "The database instance was not found on its host. Operators have been alerted.",
                retryable=False,
            ) from e
        raise JobFailed(
            "infrastructure_error",
            "An infrastructure error occurred; retrying." if e.retryable else "An infrastructure error occurred.",
            retryable=e.retryable,
        ) from e


class Runner:
    def __init__(self, db: Session, job: ProvisioningJob, database: Database, worker_id: str, pool: ProvisionerPool):
        self.db = db
        self.job = job
        self.database = database
        self.worker_id = worker_id
        self.pool = pool

    @property
    def prov(self) -> ProvisionerClient:
        """The provisioner of the host this database lives on (looked up on every call: the
        host is chosen during create and changes only in the migrate job)."""
        return self._client_for(self.database.host_id)

    def _client_for(self, host_id: uuid.UUID | None) -> ProvisionerClient:
        try:
            return self.pool.for_host_id(self.db, host_id)
        except HostUnavailable as e:
            raise JobFailed("host_unavailable", "The database's host is not reachable right now; retrying.") from e

    # --- placement -----------------------------------------------------------------------------
    def _place(self) -> None:
        """Choose a host before anything is created. Once an instance exists (endpoint recorded)
        the host is fixed; only the migrate job moves a database."""
        d = self.database
        if self.pool.override is not None and d.host_id is None:
            return  # tests with a single fake provisioner
        if d.host_id is not None:
            host = self.db.get(InfrastructureHost, d.host_id)
            usable = host is not None and host.status == "active" and is_fresh(host)
            if usable or d.internal_endpoint_host is not None:
                return
        excluded = {uuid.UUID(x) for x in (self.job.payload or {}).get("excluded_hosts", [])}

        def choose() -> InfrastructureHost:
            return choose_host(
                self.db, region=d.region, profile=d.compute_profile, storage_mb=d.allocated_storage_mb, exclude=excluded
            )

        try:
            try:
                host = choose()
            except NoCapacity:
                # Heartbeats may be stale (or no host registered yet): refresh the inventory once.
                from app.monitoring.metrics import update_host_inventory

                update_host_inventory(self.db, self.pool)
                self.db.commit()
                host = choose()
        except NoCapacity as e:
            if excluded:  # capacity may have been freed since: try every host again next time
                self.job.payload = {**(self.job.payload or {}), "excluded_hosts": []}
                self.db.commit()
            log_event(log, "placement failed", database_id=str(d.id), reason=str(e)[:300])
            raise JobFailed("capacity_exceeded", "No capacity is available right now; we'll retry shortly.") from e
        d.host_id = host.id
        sm.record_event(self.db, d, "placed", "Placed on an available host")
        self.db.commit()

    def _exclude_current_host(self) -> None:
        d = self.database
        if d.host_id is None or d.internal_endpoint_host is not None or self.pool.override is not None:
            return
        excluded = set((self.job.payload or {}).get("excluded_hosts", []))
        excluded.add(str(d.host_id))
        self.job.payload = {**(self.job.payload or {}), "excluded_hosts": sorted(excluded)}
        d.host_id = None
        self.db.commit()

    def step(self, name: str, status: str | None = None, reason: str | None = None) -> None:
        self.job.step = name
        if status and self.database.status != status:
            sm.transition(self.db, self.database, status, reason=reason)
        extend_lease(self.db, self.job, self.worker_id)
        self.db.commit()

    # --- create ------------------------------------------------------------------------------
    def create(self, restore: Callable[[DatabaseCredential], None] | None = None) -> None:
        d = self.database
        if d.status == "ready":
            return
        if d.status not in ("queued", "provisioning", "configuring", "testing"):
            raise JobFailed("invalid_state", f"Database is {d.status}.", retryable=False)
        if d.status != "queued":
            sm.transition(self.db, d, "queued", reason="Resuming after interruption")
        self._place()
        self.step("provisioning", "provisioning", "Creating an isolated PostgreSQL instance")
        try:
            info = _prov_call(
                self.prov.ensure_instance,
                d.internal_database_name,
                pg_version=d.postgres_version,
                profile=d.compute_profile,
                storage_mb=d.allocated_storage_mb,
                max_connections=d.max_connections,
                database_id=str(d.id),
                owner_id=str(d.owner_id),
            )
        except JobFailed as e:
            if e.code in ("capacity_exceeded", "provisioner_unreachable", "host_unavailable"):
                self._exclude_current_host()  # nothing was created there: place elsewhere next attempt
            raise
        d.internal_endpoint_host = info["endpoint_host"]
        d.internal_endpoint_port = info["endpoint_port"]
        d.started_at = utcnow()
        self.step("configuring", "configuring", "Creating database and credentials")
        cred = _pending_credential(self.db, d)
        self.db.commit()  # persist before use so a retry reuses the same credential
        verifier = scram_sha256_verifier(_password(self.db, cred))
        _prov_call(
            self.prov.configure,
            d.internal_database_name,
            database=d.pg_database_name,
            username=cred.username,
            scram_verifier=verifier,
            connection_limit=d.max_connections,
        )
        if restore is not None and d.data_restored_at is None:
            restore(cred)
            d.data_restored_at = utcnow()
            self.db.commit()
        self.step("testing", "testing", "Verifying connectivity over TLS")
        try:
            result = verify(d, cred.username, _password(self.db, cred))
        except ConnectivityError as e:
            raise JobFailed("connectivity_failed", "Connectivity check through the gateway failed; retrying.") from e
        cred.activated_at = utcnow()
        d.credential_reference = cred.id
        sm.transition(self.db, d, "ready", reason=f"Connected successfully ({result['version']})")
        notify(
            self.db,
            d.owner_id,
            "database_ready",
            f"Database “{d.name}” is ready",
            "Your PostgreSQL database is running with its restored data. Copy the connection URL from the dashboard."
            if d.data_restored_at
            else "Your PostgreSQL database is running. Copy the connection URL from the dashboard.",
            link=f"/databases/{d.id}",
            dedupe_key=f"db-ready:{d.id}:{self.job.id}",
        )
        audit(
            self.db,
            "database.ready",
            actor_type="system",
            tenant_user_id=d.owner_id,
            resource_type="database",
            resource_id=d.id,
        )

    # --- delete ------------------------------------------------------------------------------
    def delete(self) -> None:
        d = self.database
        if d.status == "deleted":
            return
        if d.status != "deleting":
            sm.transition(self.db, d, "deleting", reason="Deleting")
        final_id = (self.job.payload or {}).get("final_backup_id")
        if final_id:
            backup = self.db.get(Backup, uuid.UUID(final_id))
            if backup is not None and backup.status not in ("completed", "verified"):
                self.step("final_backup")
                self._run_backup(backup)
                self.db.commit()
        self.step("destroying")
        _prov_call(self.prov.destroy, d.internal_database_name)
        now = utcnow()
        for cred in self.db.scalars(select(DatabaseCredential).where(DatabaseCredential.database_id == d.id)):
            if cred.revoked_at is None:
                cred.revoked_at = now
            if cred.secret_reference:
                secret_store.destroy(self.db, cred.secret_reference)
        d.internal_endpoint_host = None
        d.credential_reference = None
        sm.transition(self.db, d, "deleted", reason="Database and storage removed")
        notify(
            self.db,
            d.owner_id,
            "database_deleted",
            f"Database “{d.name}” was deleted",
            "The instance, its storage and credentials were removed.",
            email=False,
            dedupe_key=f"db-deleted:{d.id}",
        )
        audit(
            self.db,
            "database.deleted",
            actor_type="system",
            tenant_user_id=d.owner_id,
            resource_type="database",
            resource_id=d.id,
        )

    # --- stop / start ------------------------------------------------------------------------
    def stop(self) -> None:
        d = self.database
        if d.status == "stopped":
            return
        self.step("stopping")
        _prov_call(self.prov.stop, d.internal_database_name)
        d.connections_current = 0
        sm.transition(self.db, d, "stopped", reason="Stopped")
        notify(
            self.db,
            d.owner_id,
            "database_stopped",
            f"Database “{d.name}” was stopped",
            "Connections are refused until you start it again.",
            dedupe_key=f"db-stopped:{d.id}:{self.job.id}",
        )

    def start(self) -> None:
        d = self.database
        if d.status == "ready":
            return
        if d.status in ("provisioning", "configuring", "testing"):
            sm.transition(self.db, d, "queued", reason="Resuming after interruption")
        self.step("starting", "provisioning", "Starting instance")
        info = _prov_call(self.prov.start, d.internal_database_name)
        d.internal_endpoint_host = info["endpoint_host"]
        d.internal_endpoint_port = info["endpoint_port"]
        d.started_at = utcnow()
        self.step("configuring", "configuring", "Applying configuration")
        self.step("testing", "testing", "Verifying connectivity over TLS")
        cred = _active_credential(self.db, d)
        if cred is None:
            raise JobFailed("credential_missing", "No active credential for this database.", retryable=False)
        try:
            verify(d, cred.username, _password(self.db, cred))
        except ConnectivityError as e:
            raise JobFailed("connectivity_failed", "Connectivity check through the gateway failed; retrying.") from e
        sm.transition(self.db, d, "ready", reason="Started")
        notify(
            self.db,
            d.owner_id,
            "database_started",
            f"Database “{d.name}” is running again",
            "",
            dedupe_key=f"db-started:{d.id}:{self.job.id}",
        )

    # --- rotate --------------------------------------------------------------------------------
    def rotate(self) -> None:
        d = self.database
        action = (self.job.payload or {}).get("action", "password")
        if action == "storage":
            # Apply the new allocation to the data plane (hard-limited volumes grow; a stopped
            # instance stays stopped).
            if d.status not in ("ready", "stopped"):
                raise JobFailed("invalid_state", "Database must be running or stopped.", retryable=False)
            self.step("resizing_storage")
            extend_lease(self.db, self.job, self.worker_id, long=True)
            self.db.commit()
            r = _prov_call(self.prov.set_storage, d.internal_database_name, storage_mb=d.allocated_storage_mb)
            if r.get("hard_limit") and r.get("changed"):
                note = " (the instance restarted briefly)" if r.get("mode") == "offline" else ""
                sm.record_event(
                    self.db, d, "storage_resized", f"Storage volume grown to {d.allocated_storage_mb} MB{note}"
                )
            return
        if d.status != "ready":
            raise JobFailed("invalid_state", "Database must be ready.", retryable=False)
        if action == "connection_limit":
            cred = _active_credential(self.db, d)
            if cred is None:
                raise JobFailed("credential_missing", "No active credential.", retryable=False)
            _prov_call(
                self.prov.set_connection_limit,
                d.internal_database_name,
                username=cred.username,
                connection_limit=d.max_connections,
            )
            sm.record_event(self.db, d, "connection_limit_changed", f"Connection limit set to {d.max_connections}")
            return
        self.step("rotating")
        new = _pending_credential(self.db, d)
        self.db.commit()
        _prov_call(
            self.prov.rotate,
            d.internal_database_name,
            username=new.username,
            scram_verifier=scram_sha256_verifier(_password(self.db, new)),
            terminate_sessions=True,
        )
        now = utcnow()
        old = _active_credential(self.db, d)
        if old is not None and old.id != new.id:
            old.revoked_at = now
            if old.secret_reference:
                secret_store.destroy(self.db, old.secret_reference)
            self.db.flush()
        new.activated_at = now
        new.rotated_at = now
        d.credential_reference = new.id
        sm.record_event(
            self.db,
            d,
            "credentials_rotated",
            "Password rotated; previous password revoked and existing sessions terminated",
        )
        notify(
            self.db,
            d.owner_id,
            "credentials_rotated",
            f"Credentials rotated for “{d.name}”",
            "The previous password no longer works. Update your applications with the new connection URL.",
            severity="warning",
            link=f"/databases/{d.id}?tab=connection",
            dedupe_key=f"rotated:{self.job.id}",
        )
        audit(
            self.db,
            "database.credentials_rotated",
            actor_type="system",
            tenant_user_id=d.owner_id,
            resource_type="database",
            resource_id=d.id,
        )

    # --- backups ---------------------------------------------------------------------------------
    def _run_backup(self, backup: Backup) -> None:
        """Take `backup` of self.database via the provisioner and record the result."""
        d = self.database
        backup.status = "running"
        backup.started_at = backup.started_at or utcnow()
        backup.error_message = None
        extend_lease(self.db, self.job, self.worker_id, long=True)
        self.db.commit()
        r = _prov_call(
            self.prov.create_backup,
            d.internal_database_name,
            database=d.pg_database_name,
            database_id=str(d.id),
            backup_id=str(backup.id),
        )
        now = utcnow()
        backup.status = "completed"
        backup.storage_reference = r["storage_reference"]
        backup.size = int(r["size"])
        backup.checksum_sha256 = r["sha256"]
        backup.encryption_key_id = r["key_id"]
        backup.table_count = int(r["table_count"])
        backup.duration_ms = int(r["duration_ms"])
        backup.store = r.get("store")
        backup.host_id = d.host_id
        backup.completed_at = now
        backup.expires_at = now + dt.timedelta(days=retention_days(self.db, d.owner_id))
        sm.record_event(
            self.db,
            d,
            "backup_completed",
            f"{backup.kind.replace('_', ' ').capitalize()} backup completed ({_human(backup.size)}, "
            f"{backup.table_count} tables)",
        )

    def backup(self) -> None:
        d = self.database
        backup = self._payload_backup()
        if backup.status in ("completed", "verified"):
            return
        if d.status != "ready":
            raise JobFailed(
                "invalid_state", "The database is not running, so it could not be backed up.", retryable=False
            )
        self.step("backing_up")
        self._run_backup(backup)
        if backup.kind in ("manual", "final"):
            notify(
                self.db,
                d.owner_id,
                "backup_completed",
                f"Backup of “{d.name}” completed",
                f"Encrypted backup stored ({_human(backup.size)}). You can restore it into a new database.",
                link=f"/databases/{d.id}?tab=backups",
                dedupe_key=f"backup-done:{backup.id}",
            )

    def verify_backup(self) -> None:
        backup = self._payload_backup()
        if backup.status not in ("completed", "verified"):
            raise JobFailed("invalid_state", "Only completed backups can be verified.", retryable=False)
        if backup.checksum_sha256 is None:
            raise JobFailed("invalid_state", "Backup has no checksum.", retryable=False)
        self.step("verifying")
        extend_lease(self.db, self.job, self.worker_id, long=True)
        self.db.commit()
        # Verify where the object lives (per-host local stores); shared stores work anywhere.
        r = _prov_call(
            self._client_for(backup.host_id or self.database.host_id).verify_backup,
            source_database_id=str(backup.database_id),
            backup_id=str(backup.id),
            sha256=backup.checksum_sha256,
            pg_version=backup.pg_version or self.database.postgres_version,
            expected_table_count=backup.table_count,
            storage_mb=max(self.database.allocated_storage_mb, 256),
        )
        backup.verified_at = utcnow()
        if r.get("verified"):
            backup.verification_status = "passed"
            backup.verification_error = None
            backup.status = "verified"
        else:
            backup.verification_status = "failed"
            backup.verification_error = (
                f"Restored {r.get('table_count')} tables, expected {r.get('expected_table_count')}"
            )
            audit(
                self.db,
                "backup.verification_failed",
                actor_type="system",
                tenant_user_id=backup.owner_id,
                resource_type="backup",
                resource_id=backup.id,
                outcome="failure",
                metadata={"restored_tables": r.get("table_count"), "expected": r.get("expected_table_count")},
            )

    def clone(self) -> None:
        """Provision a new database and restore data from a backup of its source."""
        d = self.database
        if d.status == "ready":
            return
        if d.source_database_id is None and d.restored_from_backup_id is None:
            raise JobFailed("invalid_state", "Not a clone.", retryable=False)
        if d.restored_from_backup_id is None:
            # Copy of current data: take a fresh backup of the source first (idempotent across retries).
            source = self.db.get(Database, d.source_database_id)
            if source is None or source.status != "ready":
                raise JobFailed(
                    "source_unavailable", "The source database is not running, so it can't be copied.", retryable=False
                )
            backup = self.db.scalar(
                select(Backup).where(
                    Backup.database_id == source.id,
                    Backup.kind == "pre_clone",
                    Backup.status.in_(("pending", "running")),
                    Backup.requested_by == d.id,
                )
            ) or _new_backup(self.db, source, "pre_clone", requested_by=d.id)
            self.db.commit()
            self.step("copying_source")
            src_runner = Runner(self.db, self.job, source, self.worker_id, self.pool)
            src_runner._run_backup(backup)
            d.restored_from_backup_id = backup.id
            sm.record_event(self.db, d, "source_backed_up", "Took a consistent backup of the source database")
            self.db.commit()
        src_backup = self.db.get(Backup, d.restored_from_backup_id)
        if src_backup is None or src_backup.status not in ("completed", "verified") or not src_backup.checksum_sha256:
            raise JobFailed("backup_unavailable", "The backup to restore is no longer available.", retryable=False)

        def restore(cred: DatabaseCredential) -> None:
            self.step("restoring", "configuring", "Restoring data from backup")
            extend_lease(self.db, self.job, self.worker_id, long=True)
            self.db.commit()
            r = self._restore_into(d.host_id, d.internal_database_name, d.pg_database_name, cred.username, src_backup)
            sm.record_event(self.db, d, "data_restored", f"Restored {r.get('table_count', 0)} tables from backup")

        self.create(restore=restore)

    def _restore_into(
        self, host_id: uuid.UUID | None, iid: str, pg_database: str, username: str, backup: Backup
    ) -> dict[str, Any]:
        """Restore `backup` into instance `iid` on `host_id`, first copying the (encrypted) object
        to that host when it sits in another host's local store. The copy is removed afterwards."""
        assert backup.checksum_sha256 is not None
        relayed = False
        if backup.store == "local" and backup.host_id is not None and backup.host_id != host_id:
            _prov_call(
                self._client_for(backup.host_id).relay_backup_to,
                self._client_for(host_id),
                database_id=str(backup.database_id),
                backup_id=str(backup.id),
                sha256=backup.checksum_sha256,
            )
            relayed = True
        try:
            return _prov_call(
                self._client_for(host_id).restore,
                iid,
                database=pg_database,
                username=username,
                source_database_id=str(backup.database_id),
                backup_id=str(backup.id),
                sha256=backup.checksum_sha256,
            )
        finally:
            if relayed:
                with contextlib.suppress(ProvisionerError):
                    self._client_for(host_id).delete_backup(str(backup.database_id), str(backup.id))

    # --- migrate (operator-initiated move to another host) -------------------------------------
    def migrate(self) -> None:
        """Move a database to `migration_target_host_id`, keeping its id, URL and credentials.

        read-only + disconnect on the source → consistent backup → new instance on the target
        (same role and password) → restore → switch host/endpoint → verify through the gateway →
        remove the old instance. Downtime ≈ backup + restore time. Before the switch every
        failure rolls back to the source (see _handle_failure)."""
        d = self.database
        payload = dict(self.job.payload or {})
        if d.status == "ready" and payload.get("switched"):
            return
        if d.status not in ("migrating", "testing"):
            raise JobFailed("invalid_state", f"Database is {d.status}.", retryable=False)
        cred = _active_credential(self.db, d)
        if cred is None:
            raise JobFailed("credential_missing", "No active credential for this database.", retryable=False)
        if not payload.get("switched"):
            target_id = d.migration_target_host_id
            if target_id is None:
                raise JobFailed("invalid_state", "No target host.", retryable=False)
            source_client = self.prov
            new_iid = payload.setdefault("new_iid", random_lower_alnum(20))
            payload.setdefault("source_host_id", str(d.host_id) if d.host_id else None)
            payload.setdefault("source_iid", d.internal_database_name)
            self.job.payload = payload
            self.step("freezing")
            _prov_call(
                source_client.set_read_only, d.internal_database_name, database=d.pg_database_name, read_only=True
            )
            _prov_call(source_client.terminate_sessions, d.internal_database_name, username=cred.username)
            # 1. consistent backup of the (now read-only) source
            backup = self.db.get(Backup, uuid.UUID(payload["backup_id"])) if payload.get("backup_id") else None
            if backup is None or backup.status not in ("completed", "verified"):
                backup = backup or _new_backup(self.db, d, "migration")
                payload["backup_id"] = str(backup.id)
                self.job.payload = payload
                self.db.commit()
                self.step("backing_up")
                self._run_backup(backup)
                self.db.commit()
            # 2. new instance on the target host with the same credentials
            self.step("provisioning_target")
            target = self._client_for(target_id)
            info = _prov_call(
                target.ensure_instance,
                new_iid,
                pg_version=d.postgres_version,
                profile=d.compute_profile,
                storage_mb=d.allocated_storage_mb,
                max_connections=d.max_connections,
                database_id=str(d.id),
                owner_id=str(d.owner_id),
            )
            _prov_call(
                target.configure,
                new_iid,
                database=d.pg_database_name,
                username=cred.username,
                scram_verifier=scram_sha256_verifier(_password(self.db, cred)),
                connection_limit=d.max_connections,
            )
            # 3. restore (idempotent across retries: a restored target is kept)
            if not payload.get("restored"):
                self.step("restoring")
                extend_lease(self.db, self.job, self.worker_id, long=True)
                self.db.commit()
                self._restore_into(target_id, new_iid, d.pg_database_name, cred.username, backup)
                payload["restored"] = True
                self.job.payload = payload
                self.db.commit()
            # 4. switch
            d.host_id = target_id
            d.internal_database_name = new_iid
            d.internal_endpoint_host = info["endpoint_host"]
            d.internal_endpoint_port = info["endpoint_port"]
            d.migration_target_host_id = None
            d.started_at = utcnow()
            payload["switched"] = True
            self.job.payload = payload
            sm.transition(self.db, d, "testing", reason="Verifying connectivity on the new host")
            self.db.commit()
        # 5. verify through the gateway, then retire the old instance
        try:
            verify(d, cred.username, _password(self.db, cred))
        except ConnectivityError as e:
            raise JobFailed("connectivity_failed", "Connectivity check on the new host failed; retrying.") from e
        sm.transition(self.db, d, "ready", reason="Moved to another host")
        self.db.commit()
        old_host = uuid.UUID(payload["source_host_id"]) if payload.get("source_host_id") else None
        try:
            self._client_for(old_host).destroy(payload["source_iid"])
        except (ProvisionerError, JobFailed):
            log_event(log, "old instance cleanup deferred to reconciler", database_id=str(d.id))
        sm.record_event(self.db, d, "migrated", "Moved to another host; connection URL and credentials unchanged")
        audit(
            self.db,
            "database.migrated",
            actor_type="system",
            tenant_user_id=d.owner_id,
            resource_type="database",
            resource_id=d.id,
            metadata={"from_host": payload.get("source_host_id"), "to_host": str(d.host_id)},
        )

    def _payload_backup(self) -> Backup:
        bid = (self.job.payload or {}).get("backup_id")
        backup = self.db.get(Backup, uuid.UUID(bid)) if bid else None
        if backup is None:
            raise JobFailed("backup_missing", "Backup record not found.", retryable=False)
        return backup


def _new_backup(db: Session, database: Database, kind: str, requested_by: uuid.UUID | None = None) -> Backup:
    from app.services.backup_service import new_backup_row

    return new_backup_row(db, database, kind=kind, requested_by=requested_by)


def retention_days(db: Session, owner_id: uuid.UUID) -> int:
    from app.services.backup_service import retention_days as rd

    return rd(db, owner_id)


def _human(n: int | None) -> str:
    if n is None:
        return "unknown size"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


HANDLERS = {
    "create": Runner.create,
    "retry": Runner.create,
    "backup": Runner.backup,
    "clone": Runner.clone,
    "verify_backup": Runner.verify_backup,
    "delete": Runner.delete,
    "stop": Runner.stop,
    "start": Runner.start,
    "rotate": Runner.rotate,
    "migrate": Runner.migrate,
}


def backoff_seconds(attempt: int) -> float:
    s = get_settings()
    return float(min(s.job_backoff_base_seconds * (2 ** max(attempt - 1, 0)), 300))


def execute(job_id: uuid.UUID, worker_id: str, prov: ProvisionerClient | ProvisionerPool | None = None) -> str:
    """Run one claimed job to completion or to its next retry. Returns the final job status.
    `prov` may be a pool, or a single client used for every host (tests)."""
    pool = prov if isinstance(prov, ProvisionerPool) else ProvisionerPool(override=prov)
    db = new_session(system=True)
    try:
        job = db.get(ProvisioningJob, job_id, with_for_update=False)
        if job is None or job.status != "running" or job.lease_owner != worker_id:
            return "skipped"
        database = db.get(Database, job.database_id)
        if database is None:
            job.status = "failed"
            job.error_code = "database_missing"
            db.commit()
            return "failed"
        log_event(
            log,
            "job started",
            job_id=str(job.id),
            kind=job.kind,
            attempt=job.attempt_count,
            database_id=str(database.id),
        )
        runner = Runner(db, job, database, worker_id, pool)
        try:
            HANDLERS[job.kind](runner)
            job.status = "succeeded"
            job.completed_at = utcnow()
            job.lease_owner = None
            job.error_code = None
            job.safe_error_message = None
            db.commit()
            log_event(log, "job succeeded", job_id=str(job.id), kind=job.kind)
            return "succeeded"
        except Exception as e:
            db.rollback()
            err = e if isinstance(e, JobFailed) else JobFailed("internal_error", "Unexpected error; retrying.")
            if not isinstance(e, JobFailed):
                log.exception("job %s crashed: %s", job_id, type(e).__name__)
            return _handle_failure(job_id, err, pool)
    finally:
        db.close()


def _handle_failure(job_id: uuid.UUID, err: JobFailed, pool: ProvisionerPool | None = None) -> str:
    pool = pool or ProvisionerPool()
    db = new_session(system=True)
    try:
        job = db.get(ProvisioningJob, job_id)
        assert job is not None
        database = db.get(Database, job.database_id)
        assert database is not None
        try:
            prov = pool.for_database(db, database)
        except HostUnavailable:
            prov = pool.default()
        job.error_code = err.code
        job.safe_error_message = err.safe_message[:500]
        job.lease_owner = None
        final = (not err.retryable) or job.attempt_count >= job.max_attempts
        log_event(
            log,
            "job attempt failed",
            job_id=str(job.id),
            kind=job.kind,
            code=err.code,
            attempt=job.attempt_count,
            final=final,
        )
        if not final:
            job.status = "queued"
            job.run_after = utcnow() + dt.timedelta(seconds=backoff_seconds(job.attempt_count))
            if job.kind in ("create", "retry", "start", "clone") and database.status in (
                "provisioning",
                "configuring",
                "testing",
            ):
                sm.transition(db, database, "queued", reason=f"Retrying: {err.safe_message}")
            else:
                sm.record_event(db, database, "attempt_failed", err.safe_message)
            db.commit()
            return "queued"
        job.status = "failed"
        job.completed_at = utcnow()
        if job.kind in ("backup", "delete", "clone"):
            _fail_backups(db, job, err)
        if job.kind in ("create", "retry", "clone"):
            # Failure cleanup: never leave uncontrolled resources behind.
            cleanup_note = "Resources cleaned up."
            try:
                prov.destroy(database.internal_database_name)
            except ProvisionerError:
                cleanup_note = "Cleanup pending (reconciler will retry)."
            for cred in db.scalars(
                select(DatabaseCredential).where(
                    DatabaseCredential.database_id == database.id, DatabaseCredential.activated_at.is_(None)
                )
            ):
                cred.revoked_at = utcnow()
                if cred.secret_reference:
                    secret_store.destroy(db, cred.secret_reference)
            database.internal_endpoint_host = None
            database.data_restored_at = None  # the instance is gone: a retry must restore again
            if database.status != "failed":
                sm.transition(db, database, "failed", reason=f"{err.safe_message} {cleanup_note}")
            notify(
                db,
                database.owner_id,
                "database_failed",
                f"Database “{database.name}” could not be created",
                f"{err.safe_message} You can retry from the dashboard; nothing was left running.",
                severity="error",
                link=f"/databases/{database.id}",
                dedupe_key=f"db-failed:{job.id}",
            )
        elif job.kind == "start":
            if database.status != "failed":
                sm.transition(db, database, "failed", reason=err.safe_message)
        elif job.kind == "stop":
            if database.status == "stopping":
                sm.transition(db, database, "ready", reason=f"Stop failed: {err.safe_message}")
        elif job.kind == "delete":
            sm.record_event(db, database, "delete_failed", f"{err.safe_message} Operators have been alerted.")
            # Keep 'deleting' so the gateway keeps refusing connections; reconciler retries.
            job2 = ProvisioningJob(
                id=uuid.uuid4(),
                database_id=database.id,
                owner_id=database.owner_id,
                kind="delete",
                status="queued",
                max_attempts=job.max_attempts,
                run_after=utcnow() + dt.timedelta(minutes=10),
                # Keep the final-backup request: data the user asked to preserve is never destroyed
                # without it (the retry takes the backup again before destroying anything).
                payload={**(job.payload or {}), "retry": True},
            )
            db.add(job2)
        elif job.kind == "backup":
            sm.record_event(db, database, "backup_failed", err.safe_message)
        elif job.kind == "migrate":
            _rollback_migration(db, pool, job, database, err)
        elif job.kind == "verify_backup":
            bid = (job.payload or {}).get("backup_id")
            b = db.get(Backup, uuid.UUID(bid)) if bid else None
            if b is not None:
                b.verification_error = f"Verification could not run: {err.safe_message}"[:500]
        else:
            sm.record_event(db, database, "operation_failed", err.safe_message)
        audit(
            db,
            f"job.{job.kind}.failed",
            actor_type="system",
            tenant_user_id=database.owner_id,
            resource_type="database",
            resource_id=database.id,
            outcome="failure",
            metadata={"code": err.code, "attempts": job.attempt_count},
        )
        db.commit()
        return "failed"
    finally:
        db.close()


def _rollback_migration(
    db: Session, pool: ProvisionerPool, job: ProvisioningJob, database: Database, err: JobFailed
) -> None:
    payload = job.payload or {}
    if payload.get("switched"):
        # Already running on the new host; connectivity keeps being retried by operators.
        sm.record_event(db, database, "migration_verify_failed", f"{err.safe_message} Operators have been alerted.")
        return
    note = "The database stayed on its original host."
    try:
        source = pool.for_database(db, database)
        source.set_read_only(database.internal_database_name, database=database.pg_database_name, read_only=False)
    except (ProvisionerError, HostUnavailable):
        note += " Write access could not be restored automatically; operators have been alerted."
    if payload.get("new_iid") and database.migration_target_host_id:
        try:
            pool.for_host_id(db, database.migration_target_host_id).destroy(payload["new_iid"])
        except (ProvisionerError, HostUnavailable):
            note += " Target cleanup pending (reconciler)."
    database.migration_target_host_id = None
    if database.status == "migrating":
        sm.transition(db, database, "ready", reason=f"Move failed: {err.safe_message} {note}")


def _fail_backups(db: Session, job: ProvisioningJob, err: JobFailed) -> None:
    """Mark the backup(s) a finally-failed job was producing as failed and tell the owner."""
    payload = job.payload or {}
    ids = [payload.get("backup_id"), payload.get("final_backup_id")]
    rows = [db.get(Backup, uuid.UUID(i)) for i in ids if i]
    if job.kind == "clone":
        rows += list(
            db.scalars(
                select(Backup).where(
                    Backup.kind == "pre_clone",
                    Backup.requested_by == job.database_id,
                    Backup.status.in_(("pending", "running")),
                )
            )
        )
    for b in rows:
        if b is None or b.status not in ("pending", "running"):
            continue
        b.status = "failed"
        b.error_message = err.safe_message[:500]
        b.completed_at = utcnow()
        src = db.get(Database, b.database_id)
        if b.kind in ("scheduled", "manual", "final") and src is not None:
            notify(
                db,
                b.owner_id,
                "backup_failed",
                f"Backup of “{src.name}” failed",
                f"{err.safe_message} We'll try again at the next scheduled backup; operators have been alerted.",
                severity="error",
                link=f"/databases/{src.id}?tab=backups",
                # One alert per database per day for scheduled failures; every manual/final failure is reported.
                dedupe_key=f"backup-failed:{b.database_id}:{utcnow():%Y%m%d}"
                if b.kind == "scheduled"
                else f"backup-failed:{b.id}",
            )


def release_stale_leases_for(worker_id: str) -> None:
    """On clean shutdown: requeue running jobs owned by this worker."""
    db = new_session(system=True)
    try:
        db.execute(
            update(ProvisioningJob)
            .where(ProvisioningJob.status == "running", ProvisioningJob.lease_owner == worker_id)
            .values(status="queued", lease_owner=None, run_after=utcnow())
        )
        db.commit()
    finally:
        db.close()

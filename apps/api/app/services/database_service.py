"""Database resource operations (control plane side). Infrastructure work happens in the worker."""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ApiError, Conflict, NotFound
from app.core.redis import wake_workers
from app.models import Database, DatabaseCredential, InfrastructureHost, Project, ProvisioningJob, utcnow
from app.provisioning import state_machine as sm
from app.provisioning.connection import public_host
from app.schemas.resources import DatabaseCreate, DatabaseOut, DatabaseUpdate, ProvisioningStatus
from app.security.deps import Principal
from app.security.tokens import random_id, random_lower_alnum
from app.services import plans
from app.services.audit import audit

STAGES = ["pending", "validating", "queued", "provisioning", "configuring", "testing", "ready"]
STAGE_LABELS = {
    "pending": "Request received",
    "validating": "Validating plan and quota",
    "queued": "Waiting for a provisioning worker",
    "provisioning": "Creating an isolated PostgreSQL instance",
    "configuring": "Creating your database and credentials",
    "testing": "Verifying connectivity over TLS",
    "ready": "Ready",
    "failed": "Provisioning failed",
    "stopping": "Stopping",
    "stopped": "Stopped",
    "deleting": "Deleting",
    "deleted": "Deleted",
    "migrating": "Moving to another server (read-only, then briefly unavailable)",
}


def slug_pg_name(name: str) -> str:
    s = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")
    if not s or not s[0].isalpha():
        s = "db_" + s
    s = s[:63].rstrip("_") or "app"
    if s in ("postgres", "template0", "template1"):
        s = f"{s}_db"
    return s


def get_project_for(db: Session, principal: Principal, project_id: uuid.UUID) -> Project:
    project = db.scalar(
        select(Project).where(
            Project.id == project_id, Project.owner_id == principal.user.id, Project.deleted_at.is_(None)
        )
    )
    if project is None or not principal.can_access_project(project.id):
        raise NotFound("Project not found.")
    return project


def get_database_for(
    db: Session, principal: Principal, database_id: uuid.UUID, *, include_deleted: bool = False, lock: bool = False
) -> Database:
    stmt = select(Database).where(Database.id == database_id, Database.owner_id == principal.user.id)
    if not include_deleted:
        stmt = stmt.where(Database.status != "deleted")
    if lock:
        stmt = stmt.with_for_update()
    database = db.scalar(stmt)
    if database is None or not principal.can_access_project(database.project_id):
        raise NotFound("Database not found.")
    return database


def active_credential(db: Session, database: Database) -> DatabaseCredential | None:
    return db.scalar(
        select(DatabaseCredential).where(
            DatabaseCredential.database_id == database.id,
            DatabaseCredential.activated_at.is_not(None),
            DatabaseCredential.revoked_at.is_(None),
        )
    )


def latest_job(db: Session, database_id: uuid.UUID) -> ProvisioningJob | None:
    return db.scalar(
        select(ProvisioningJob)
        .where(ProvisioningJob.database_id == database_id)
        .order_by(ProvisioningJob.created_at.desc())
        .limit(1)
    )


def create_job(
    db: Session,
    database: Database,
    kind: str,
    *,
    idempotency_key: str | None = None,
    payload: dict[str, Any] | None = None,
) -> ProvisioningJob:
    job = ProvisioningJob(
        id=uuid.uuid4(),
        database_id=database.id,
        owner_id=database.owner_id,
        kind=kind,
        status="queued",
        idempotency_key=idempotency_key,
        payload=payload or {},
        max_attempts=get_settings().job_max_attempts,
        run_after=utcnow(),
    )
    db.add(job)
    db.flush()
    return job


def provisioning_status(database: Database, job: ProvisioningJob | None) -> ProvisioningStatus:
    stage = database.status
    step = STAGES.index(stage) + 1 if stage in STAGES else (len(STAGES) if stage == "ready" else 0)
    ps = ProvisioningStatus(stage=stage, step=step, total_steps=len(STAGES), message=STAGE_LABELS.get(stage))
    if job is not None:
        ps.job_id = job.id
        ps.job_kind = job.kind
        ps.job_status = job.status
        ps.attempt = job.attempt_count
        ps.max_attempts = job.max_attempts
        ps.error_code = job.error_code
        if job.status == "queued" and job.attempt_count > 0:
            ps.next_retry_at = job.run_after
        if job.safe_error_message and job.status in ("failed", "queued"):
            ps.message = f"{STAGE_LABELS.get(stage)} — {job.safe_error_message}"
    return ps


def _host_status(db: Session, database: Database) -> str:
    if database.host_id is None:
        return "unknown"
    status = db.scalar(select(InfrastructureHost.status).where(InfrastructureHost.id == database.host_id))
    return "unavailable" if status == "offline" else "ok" if status else "unknown"


def to_out(db: Session, database: Database, *, with_job: bool = True, project_name: str | None = None) -> DatabaseOut:
    s = get_settings()
    prof = plans.PROFILES.get(database.compute_profile, {})
    if project_name is None:
        project_name = db.scalar(select(Project.name).where(Project.id == database.project_id))
    return DatabaseOut(
        id=database.id,
        project_id=database.project_id,
        project_name=project_name,
        name=database.name,
        database_name=database.pg_database_name,
        environment=database.environment,
        postgres_version=database.postgres_version,
        status=database.status,
        status_reason=database.status_reason,
        region=database.region,
        region_label=s.region_label if database.region == s.region else None,
        compute_profile=database.compute_profile,
        cpus=prof.get("cpus"),
        memory_mb=prof.get("memory_mb"),
        allocated_storage_mb=database.allocated_storage_mb,
        storage_used_bytes=database.storage_used_bytes,
        storage_over_limit=database.storage_over_limit,
        max_connections=database.max_connections,
        connections_current=database.connections_current,
        host=public_host(database),
        port=s.db_public_port,
        created_at=database.created_at,
        updated_at=database.updated_at,
        ready_at=database.ready_at,
        started_at=database.started_at,
        last_activity_at=database.last_activity_at,
        metrics_measured_at=database.metrics_measured_at,
        suspended_by_admin=database.suspended_by_admin,
        host_status=_host_status(db, database),
        source_database_id=database.source_database_id,
        restored_from_backup_id=database.restored_from_backup_id,
        data_restored_at=database.data_restored_at,
        provisioning=provisioning_status(database, latest_job(db, database.id)) if with_job else None,
    )


def create_database(
    db: Session, principal: Principal, body: DatabaseCreate, *, idempotency_key: str | None
) -> Database:
    s = get_settings()
    user_id = principal.user.id
    project = get_project_for(db, principal, body.project_id)
    if project.status != "active":
        raise Conflict("Databases can only be created in active projects.", code="project_not_active")
    version = body.postgres_version or s.default_pg_version
    if version not in s.supported_pg_versions:
        raise ApiError(
            f"PostgreSQL {version} is not offered. Choose one of: {', '.join(s.supported_pg_versions)}.",
            code="unsupported_version",
            status_code=422,
        )
    if body.region and body.region != s.region:
        raise ApiError("That region is not available.", code="unsupported_region", status_code=422)
    if body.compute_profile not in plans.PROFILES:
        raise ApiError("Unknown compute size.", code="unsupported_profile", status_code=422)

    # Quota check with the subscription row locked (serializes concurrent creates per user).
    ent = plans.get_subscription(db, user_id, lock=True)
    ent_obj = plans.effective_entitlements(db, ent)
    storage_mb = body.storage_mb or min(
        ent_obj["max_storage_per_db_mb"],
        max(256, ent_obj["max_total_storage_mb"] - plans.usage_counts(db, user_id)["allocated_storage_mb"]),
        1024,
    )
    ent_obj = plans.check_can_create_database(db, user_id, storage_mb=storage_mb, profile=body.compute_profile)

    exists = db.scalar(
        select(Database.id).where(
            Database.project_id == project.id,
            func.lower(Database.name) == body.name.lower(),
            Database.deleted_at.is_(None),
        )
    )
    if exists:
        raise Conflict("A database with this name already exists in the project.", code="name_taken")

    route_id = "db-" + random_id(10)
    database = Database(
        id=uuid.uuid4(),
        project_id=project.id,
        owner_id=user_id,
        name=body.name,
        environment=body.environment or project.environment,
        postgres_version=version,
        status="pending",
        provider="systemd",
        region=s.region,
        route_id=route_id,
        internal_database_name=random_lower_alnum(20),
        pg_database_name=body.database_name or slug_pg_name(body.name),
        compute_profile=body.compute_profile,
        allocated_storage_mb=storage_mb,
        max_connections=int(ent_obj["max_connections_per_db"]),
    )
    db.add(database)
    db.flush()
    sm.record_event(db, database, "requested", "Database creation requested", actor_type=principal.actor_type)
    sm.transition(db, database, "validating", reason="Validating plan and quota")
    sm.transition(db, database, "queued", reason="Queued for provisioning")
    create_job(db, database, "create", idempotency_key=idempotency_key)
    audit(
        db,
        "database.create",
        principal=principal,
        resource_type="database",
        resource_id=database.id,
        metadata={
            "name": database.name,
            "project_id": str(project.id),
            "version": version,
            "profile": database.compute_profile,
            "storage_mb": storage_mb,
        },
    )
    return database


def update_database(db: Session, principal: Principal, database: Database, body: DatabaseUpdate) -> Database:
    changes: dict[str, Any] = {}
    if body.name is not None and body.name != database.name:
        clash = db.scalar(
            select(Database.id).where(
                Database.project_id == database.project_id,
                func.lower(Database.name) == body.name.lower(),
                Database.deleted_at.is_(None),
                Database.id != database.id,
            )
        )
        if clash:
            raise Conflict("A database with this name already exists in the project.", code="name_taken")
        changes["name"] = (database.name, body.name)
        database.name = body.name
    if body.environment is not None and body.environment != database.environment:
        changes["environment"] = (database.environment, body.environment)
        database.environment = body.environment
    ent = plans.entitlements_for(db, principal.user.id)
    if body.storage_mb is not None and body.storage_mb != database.allocated_storage_mb:
        if body.storage_mb > ent["max_storage_per_db_mb"]:
            raise ApiError(
                f"Storage per database is limited to {ent['max_storage_per_db_mb']} MB on your plan.",
                code="quota_exceeded",
                status_code=402,
            )
        counts = plans.usage_counts(db, principal.user.id)
        if (
            counts["allocated_storage_mb"] - database.allocated_storage_mb + body.storage_mb
            > ent["max_total_storage_mb"]
        ):
            raise ApiError("Not enough storage left on your plan.", code="quota_exceeded", status_code=402)
        if database.storage_used_bytes and body.storage_mb * 1024 * 1024 < database.storage_used_bytes:
            raise ApiError(
                "New allocation is smaller than the storage already used.", code="storage_in_use", status_code=409
            )
        if database.status not in ("ready", "stopped"):
            raise Conflict("Storage can be changed while the database is running or stopped.", code="invalid_state")
        changes["storage_mb"] = (database.allocated_storage_mb, body.storage_mb)
        database.allocated_storage_mb = body.storage_mb
        create_job(db, database, "rotate", payload={"action": "storage"})
    if body.max_connections is not None and body.max_connections != database.max_connections:
        if body.max_connections > ent["max_connections_per_db"]:
            raise ApiError(
                f"Your plan allows up to {ent['max_connections_per_db']} connections per database.",
                code="quota_exceeded",
                status_code=402,
            )
        if database.status != "ready":
            raise Conflict("Connection limits can be changed while the database is ready.", code="invalid_state")
        changes["max_connections"] = (database.max_connections, body.max_connections)
        database.max_connections = body.max_connections
        create_job(db, database, "rotate", payload={"action": "connection_limit"})
    if changes:
        sm.record_event(
            db, database, "settings_changed", "Updated " + ", ".join(changes), actor_type=principal.actor_type
        )
        audit(
            db,
            "database.update",
            principal=principal,
            resource_type="database",
            resource_id=database.id,
            metadata={k: f"{a} -> {b}" for k, (a, b) in changes.items()},
        )
    return database


def request_delete(
    db: Session,
    principal: Principal,
    database: Database,
    *,
    reason: str = "user request",
    actor_type: str | None = None,
    final_backup: bool = False,
) -> ProvisioningJob:
    if database.status in ("deleting", "deleted"):
        raise Conflict("This database is already being deleted.", code="invalid_state")
    if final_backup and database.status != "ready":
        raise Conflict(
            "A final backup can only be taken while the database is running. Start it first, or delete "
            "without a final backup.",
            code="invalid_state",
        )
    # Cancel queued work for this database — deletion supersedes it.
    for job in db.scalars(
        select(ProvisioningJob).where(ProvisioningJob.database_id == database.id, ProvisioningJob.status == "queued")
    ):
        job.status = "cancelled"
        job.completed_at = utcnow()
    payload: dict[str, Any] = {"reason": reason}
    if final_backup:
        from app.services.backup_service import new_backup_row

        backup = new_backup_row(db, database, kind="final", requested_by=principal.user.id)
        payload["final_backup_id"] = str(backup.id)
    sm.transition(db, database, "deleting", reason="Deletion requested", actor_type=actor_type or principal.actor_type)
    job = create_job(db, database, "delete", payload=payload)
    audit(
        db,
        "database.delete",
        principal=principal,
        resource_type="database",
        resource_id=database.id,
        metadata={"name": database.name, "final_backup": final_backup},
        reason=reason,
    )
    return job


def request_stop(db: Session, principal: Principal, database: Database) -> ProvisioningJob:
    if database.status != "ready":
        raise Conflict("Only a ready database can be stopped.", code="invalid_state")
    sm.transition(db, database, "stopping", reason="Stop requested", actor_type=principal.actor_type)
    job = create_job(db, database, "stop")
    audit(db, "database.stop", principal=principal, resource_type="database", resource_id=database.id)
    return job


def request_start(db: Session, principal: Principal, database: Database) -> ProvisioningJob:
    if database.status != "stopped":
        raise Conflict("Only a stopped database can be started.", code="invalid_state")
    if database.suspended_by_admin:
        raise Conflict("This database was suspended by DatabaseMax support. Contact support.", code="suspended")
    sm.transition(db, database, "queued", reason="Start requested", actor_type=principal.actor_type)
    job = create_job(db, database, "start")
    audit(db, "database.start", principal=principal, resource_type="database", resource_id=database.id)
    return job


def request_rotation(db: Session, principal: Principal, database: Database) -> ProvisioningJob:
    if database.status != "ready":
        raise Conflict("Credentials can be rotated while the database is ready.", code="invalid_state")
    pending = db.scalar(
        select(ProvisioningJob.id).where(
            ProvisioningJob.database_id == database.id,
            ProvisioningJob.kind == "rotate",
            ProvisioningJob.status.in_(("queued", "running")),
        )
    )
    if pending:
        raise Conflict("A credential rotation is already in progress.", code="rotation_in_progress")
    job = create_job(db, database, "rotate", payload={"action": "password"})
    sm.record_event(
        db, database, "credentials_rotation_requested", "Credential rotation requested", actor_type=principal.actor_type
    )
    audit(db, "database.credentials_rotate", principal=principal, resource_type="database", resource_id=database.id)
    return job


def provision_kind(database: Database) -> str:
    """Clones are provisioned by the 'clone' job (create + restore); everything else by 'create'."""
    return "clone" if database.source_database_id is not None or database.restored_from_backup_id else "create"


def retry_failed(db: Session, database: Database, *, actor_type: str) -> ProvisioningJob:
    if database.status != "failed":
        raise Conflict("Only failed databases can be retried.", code="invalid_state")
    sm.transition(db, database, "queued", reason="Retry requested", actor_type=actor_type)
    return create_job(db, database, provision_kind(database))


def after_commit_wake() -> None:
    wake_workers()

"""Backups, restore verification and clones (control-plane side).

The control plane only records metadata and schedules work. Backup contents are produced,
encrypted, stored and restored by the provisioner; they never pass through this service.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Any

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session, aliased

from app.core.config import get_settings
from app.core.errors import ApiError, Conflict, NotFound
from app.core.logging import log_event
from app.models import Backup, Database, Project, ProvisioningJob, utcnow
from app.provisioning import state_machine as sm
from app.provisioning.client import ProvisionerClient, ProvisionerError
from app.provisioning.hosts import HostUnavailable, ProvisionerPool
from app.schemas.resources import BackupOut, BackupSummaryOut, CloneCreate
from app.security.deps import Principal
from app.security.tokens import random_id, random_lower_alnum
from app.services import plans
from app.services.audit import audit
from app.services.database_service import create_job, get_project_for, slug_pg_name

log = logging.getLogger("dmx.backups")

RESTORABLE = ("completed", "verified")
USER_TRIGGERED = ("manual", "pre_clone", "final")


def user_backup_bytes(db: Session, owner_id: uuid.UUID) -> int:
    return int(
        db.scalar(
            select(func.coalesce(func.sum(Backup.size), 0)).where(
                Backup.owner_id == owner_id, Backup.status.in_(RESTORABLE), Backup.kind.in_(USER_TRIGGERED)
            )
        )
        or 0
    )


def check_backup_storage_quota(db: Session, owner_id: uuid.UUID) -> None:
    """On-demand, pre-copy and final backups share a per-account storage allowance (daily
    backups are bounded by retention and don't count). Users can delete backups to free it."""
    ent = plans.entitlements_for(db, owner_id)
    limit_mb = int(ent.get("user_backup_storage_mb") or 2 * int(ent["max_total_storage_mb"]))
    if user_backup_bytes(db, owner_id) >= limit_mb * 2**20:
        raise ApiError(
            f"Your on-demand and final backups use your plan's {limit_mb} MB backup allowance. Delete older "
            "backups or wait for them to expire.",
            code="quota_exceeded",
            status_code=402,
            details={"limit_mb": limit_mb, "resource": "backup_storage"},
        )


SCHEDULE_TEXT = "Daily, automatically"


def backup_out(b: Backup, database: Database | None = None) -> BackupOut:
    return BackupOut(
        id=b.id,
        database_id=b.database_id,
        database_name=database.name if database else None,
        database_status=database.status if database else None,
        source_storage_mb=database.allocated_storage_mb if database else None,
        kind=b.kind,
        status=b.status,
        size_bytes=b.size,
        table_count=b.table_count,
        postgres_version=b.pg_version,
        created_at=b.created_at,
        started_at=b.started_at,
        completed_at=b.completed_at,
        duration_ms=b.duration_ms,
        expires_at=b.expires_at,
        verified_at=b.verified_at,
        verification_status=b.verification_status,
        error_message=b.error_message,
        restorable=b.status in RESTORABLE and (b.expires_at is None or b.expires_at > utcnow()),
    )


def retention_days(db: Session, owner_id: uuid.UUID) -> int:
    return int(plans.entitlements_for(db, owner_id).get("backup_retention_days", 7))


def new_backup_row(db: Session, database: Database, *, kind: str, requested_by: uuid.UUID | None = None) -> Backup:
    b = Backup(
        id=uuid.uuid4(),
        database_id=database.id,
        owner_id=database.owner_id,
        kind=kind,
        status="pending",
        pg_version=database.postgres_version,
        requested_by=requested_by,
    )
    db.add(b)
    db.flush()
    return b


def _in_progress(db: Session, database_id: uuid.UUID) -> bool:
    return (
        db.scalar(select(Backup.id).where(Backup.database_id == database_id, Backup.status.in_(("pending", "running"))))
        is not None
    )


def manual_backups_today(db: Session, database_id: uuid.UUID) -> int:
    since = utcnow() - dt.timedelta(hours=24)
    return (
        db.scalar(
            select(func.count(Backup.id)).where(
                Backup.database_id == database_id,
                Backup.kind == "manual",
                Backup.created_at >= since,
                Backup.status != "failed",
            )
        )
        or 0
    )


def request_backup(
    db: Session,
    database: Database,
    *,
    kind: str,
    principal: Principal | None = None,
) -> tuple[Backup, ProvisioningJob]:
    if database.status != "ready":
        raise Conflict("Backups can be taken while the database is running.", code="invalid_state")
    if _in_progress(db, database.id):
        raise Conflict("A backup of this database is already in progress.", code="backup_in_progress")
    if kind == "manual":
        ent = plans.entitlements_for(db, database.owner_id)
        limit = int(ent.get("manual_backups_per_day", 1))
        if manual_backups_today(db, database.id) >= limit:
            raise ApiError(
                f"Your {ent.plan_name} plan allows {limit} on-demand backup(s) per database per day. "
                "Daily automatic backups continue as scheduled.",
                code="quota_exceeded",
                status_code=402,
                details={"limit": limit, "resource": "manual_backups"},
            )
    if kind in USER_TRIGGERED:
        check_backup_storage_quota(db, database.owner_id)
    b = new_backup_row(db, database, kind=kind, requested_by=principal.user.id if principal else None)
    job = create_job(db, database, "backup", payload={"backup_id": str(b.id)})
    if principal is not None:
        sm.record_event(db, database, "backup_requested", "On-demand backup requested", actor_type=principal.actor_type)
        audit(
            db,
            "backup.create",
            principal=principal,
            resource_type="database",
            resource_id=database.id,
            metadata={"backup_id": str(b.id)},
        )
    return b, job


def delete_backup(db: Session, principal: Principal, backup: Backup) -> None:
    if backup.status in ("pending", "running"):
        raise Conflict("This backup is still running.", code="invalid_state")
    if backup.status in ("deleted", "expired"):
        return
    backup.status = "deleted"
    backup.expires_at = utcnow()  # the expiry task removes the object
    audit(
        db,
        "backup.delete",
        principal=principal,
        resource_type="backup",
        resource_id=backup.id,
        metadata={"database_id": str(backup.database_id), "kind": backup.kind},
    )


def get_backup_for(db: Session, principal: Principal, backup_id: uuid.UUID) -> tuple[Backup, Database]:
    row = db.execute(
        select(Backup, Database)
        .join(Database, Database.id == Backup.database_id)
        .where(Backup.id == backup_id, Backup.owner_id == principal.user.id)
    ).first()
    if row is None or not principal.can_access_project(row[1].project_id) or row[0].status == "deleted":
        raise NotFound("Backup not found.")
    return row[0], row[1]


def summary(db: Session, database: Database) -> BackupSummaryOut:
    ent = plans.entitlements_for(db, database.owner_id)
    visible = (Backup.database_id == database.id, Backup.status.in_(RESTORABLE))
    last = db.scalar(select(func.max(Backup.completed_at)).where(*visible))
    total = db.scalar(select(func.coalesce(func.sum(Backup.size), 0)).where(*visible)) or 0
    count = db.scalar(select(func.count(Backup.id)).where(*visible)) or 0
    next_due = None
    if database.status == "ready":
        # Mirrors schedule_daily_backups: 24 h after the most recent backup of any kind, or
        # 30 min after the database first became ready.
        latest_any = db.scalar(
            select(func.max(Backup.created_at)).where(
                Backup.database_id == database.id, Backup.status.in_(("pending", "running", *RESTORABLE))
            )
        )
        if latest_any is not None:
            next_due = latest_any + dt.timedelta(hours=24)
        else:
            next_due = (database.ready_at or utcnow()) + dt.timedelta(minutes=30)
        next_due = max(next_due, utcnow())
    return BackupSummaryOut(
        retention_days=int(ent.get("backup_retention_days", 7)),
        manual_backups_per_day=int(ent.get("manual_backups_per_day", 1)),
        manual_backups_today=manual_backups_today(db, database.id),
        schedule=SCHEDULE_TEXT,
        last_backup_at=last,
        next_backup_due_at=next_due,
        total_size_bytes=int(total),
        count=int(count),
    )


# --- clones -------------------------------------------------------------------------------------


def request_clone(db: Session, principal: Principal, source: Database, body: CloneCreate) -> Database:
    """Create a new database that will be restored from a backup of `source`."""
    s = get_settings()
    if source.suspended_by_admin:
        audit(
            db,
            "database.clone_refused",
            principal=principal,
            resource_type="database",
            resource_id=source.id,
            outcome="failure",
            metadata={"reason": "source suspended by support"},
        )
        raise Conflict(
            "This database was suspended by DatabaseMax support, so it can't be copied or restored. Contact support.",
            code="suspended",
        )
    backup: Backup | None = None
    if body.backup_id is not None:
        backup = db.scalar(select(Backup).where(Backup.id == body.backup_id, Backup.database_id == source.id))
        if backup is None or backup.status == "deleted":
            raise NotFound("Backup not found.")
        if backup.status not in RESTORABLE or (backup.expires_at and backup.expires_at <= utcnow()):
            raise Conflict("This backup can't be restored (it is not complete or has expired).", code="not_restorable")
    else:
        if source.status != "ready":
            raise Conflict(
                "Copying current data needs the source database to be running. Choose a backup instead.",
                code="invalid_state",
            )
        if _in_progress(db, source.id):
            raise Conflict("A backup of the source database is in progress; try again shortly.", code="busy")
        check_backup_storage_quota(db, source.owner_id)  # the copy takes a pre-clone backup
    project = get_project_for(db, principal, body.project_id or source.project_id)
    if project.status != "active":
        raise Conflict("Databases can only be created in active projects.", code="project_not_active")
    profile = body.compute_profile or source.compute_profile
    if profile not in plans.PROFILES:
        raise ApiError("Unknown compute size.", code="unsupported_profile", status_code=422)
    pg_version = backup.pg_version if backup and backup.pg_version else source.postgres_version
    if pg_version not in s.supported_pg_versions:
        raise ApiError(f"PostgreSQL {pg_version} is no longer offered.", code="unsupported_version", status_code=422)

    storage_mb = source.allocated_storage_mb
    ent = plans.check_can_create_database(db, principal.user.id, storage_mb=storage_mb, profile=profile)
    clash = db.scalar(
        select(Database.id).where(
            Database.project_id == project.id,
            func.lower(Database.name) == body.name.lower(),
            Database.deleted_at.is_(None),
        )
    )
    if clash:
        raise Conflict("A database with this name already exists in the project.", code="name_taken")

    target = Database(
        id=uuid.uuid4(),
        project_id=project.id,
        owner_id=principal.user.id,
        name=body.name,
        environment=body.environment or project.environment,
        postgres_version=pg_version,
        status="pending",
        provider="systemd",
        region=s.region,
        route_id="db-" + random_id(10),
        internal_database_name=random_lower_alnum(20),
        pg_database_name=source.pg_database_name or slug_pg_name(body.name),
        compute_profile=profile,
        allocated_storage_mb=storage_mb,
        max_connections=int(ent["max_connections_per_db"]),
        source_database_id=source.id,
        restored_from_backup_id=backup.id if backup else None,
    )
    db.add(target)
    db.flush()
    what = f"backup from {backup.completed_at:%Y-%m-%d %H:%M} UTC" if backup and backup.completed_at else "current data"
    sm.record_event(
        db, target, "requested", f"Clone of “{source.name}” requested ({what})", actor_type=principal.actor_type
    )
    sm.transition(db, target, "validating", reason="Validating plan and quota")
    sm.transition(db, target, "queued", reason="Queued for provisioning")
    create_job(db, target, "clone")
    sm.record_event(db, source, "cloned", f"Cloned into “{target.name}” ({what})", actor_type=principal.actor_type)
    audit(
        db,
        "database.clone",
        principal=principal,
        resource_type="database",
        resource_id=target.id,
        metadata={
            "source_database_id": str(source.id),
            "backup_id": str(backup.id) if backup else None,
            "name": target.name,
        },
    )
    return target


# --- scheduling (worker) ------------------------------------------------------------------------


def schedule_daily_backups(db: Session, *, limit: int = 50) -> int:
    """Queue a scheduled backup for every running database without a recent one."""
    now = utcnow()
    recent = aliased(Backup)
    has_recent = exists().where(
        recent.database_id == Database.id,
        recent.created_at > now - dt.timedelta(hours=24),
        recent.status.in_(("pending", "running", "completed", "verified")),
    )
    failed_recently = exists().where(
        recent.database_id == Database.id,
        recent.kind == "scheduled",
        recent.status == "failed",
        recent.created_at > now - dt.timedelta(hours=6),  # back off after a failure
    )
    candidates = db.scalars(
        select(Database)
        .where(
            Database.status == "ready",
            Database.suspended_by_admin.is_(False),
            Database.ready_at < now - dt.timedelta(minutes=30),
            ~has_recent,
            ~failed_recently,
        )
        .order_by(Database.ready_at)
        .limit(limit)
    ).all()
    n = 0
    for d in candidates:
        try:
            request_backup(db, d, kind="scheduled")
            n += 1
        except (Conflict, ApiError):
            continue
    return n


def schedule_verifications(db: Session, *, limit: int = 3, every_days: int = 7) -> int:
    """Restore-test the latest backup of databases that haven't had one verified recently."""
    now = utcnow()
    verified = aliased(Backup)
    recently_verified = exists().where(
        verified.database_id == Backup.database_id,
        verified.verification_status.is_not(None),
        verified.verified_at > now - dt.timedelta(days=every_days),
    )
    pending_verify = exists().where(
        ProvisioningJob.kind == "verify_backup",
        ProvisioningJob.database_id == Backup.database_id,
        ProvisioningJob.status.in_(("queued", "running")),
    )
    rows = db.scalars(
        select(Backup)
        .where(
            Backup.status == "completed",
            Backup.verification_status.is_(None),
            Backup.completed_at > now - dt.timedelta(days=2),
            ~recently_verified,
            ~pending_verify,
        )
        .order_by(Backup.completed_at.desc())
        .limit(limit * 5)
    ).all()
    seen: set[uuid.UUID] = set()
    n = 0
    for b in rows:
        if b.database_id in seen or n >= limit:
            continue
        seen.add(b.database_id)
        database = db.get(Database, b.database_id)
        if database is None:
            continue
        request_verification(db, database, b)
        n += 1
    return n


def request_verification(db: Session, database: Database, backup: Backup) -> ProvisioningJob:
    if backup.status not in RESTORABLE:
        raise Conflict("Only completed backups can be verified.", code="invalid_state")
    return create_job(db, database, "verify_backup", payload={"backup_id": str(backup.id)})


def expire_backups(db: Session, prov: ProvisionerClient | ProvisionerPool, *, limit: int = 200) -> dict[str, int]:
    """Delete backup objects past their retention, and fail backups stuck without a job."""
    pool = prov if isinstance(prov, ProvisionerPool) else ProvisionerPool(override=prov)
    now = utcnow()
    stats = {"expired": 0, "stuck_failed": 0, "errors": 0}

    def delete_object(b: Backup) -> None:
        # The object lives in the store of the host that took the backup.
        try:
            client = pool.for_host_id(db, b.host_id)
        except HostUnavailable as e:
            raise ProvisionerError("host_unavailable", str(e)) from e
        client.delete_backup(str(b.database_id), str(b.id))

    for b in db.scalars(
        select(Backup)
        .where(
            ((Backup.status.in_(RESTORABLE)) & Backup.expires_at.is_not(None) & (Backup.expires_at <= now))
            | ((Backup.status == "deleted") & Backup.storage_reference.is_not(None))
        )
        .limit(limit)
    ):
        try:
            delete_object(b)
        except ProvisionerError as e:
            stats["errors"] += 1
            log_event(log, "backup expiry failed", backup_id=str(b.id), code=e.code)
            continue
        if b.status != "deleted":
            b.status = "expired"
        b.storage_reference = None
        stats["expired"] += 1
    # Failed backups may still have left an object behind (e.g. the provisioner finished but the
    # result was never recorded): purge them once, an hour after they failed.
    stats["purged"] = 0
    for b in db.scalars(
        select(Backup)
        .where(
            Backup.status == "failed",
            Backup.completed_at < now - dt.timedelta(hours=1),
            Backup.store.is_distinct_from("purged"),
        )
        .limit(limit)
    ):
        try:
            delete_object(b)
        except ProvisionerError:
            stats["errors"] += 1
            continue
        b.store = "purged"
        stats["purged"] += 1
    # Pending/running rows whose job is gone (e.g. cancelled by a deletion) are closed out.
    live_job = exists().where(
        ProvisioningJob.database_id == Backup.database_id,
        ProvisioningJob.status.in_(("queued", "running")),
        ProvisioningJob.kind.in_(("backup", "delete", "clone")),
    )
    for b in db.scalars(
        select(Backup).where(
            Backup.status.in_(("pending", "running")),
            Backup.created_at < now - dt.timedelta(minutes=30),
            ~live_job,
        )
    ):
        b.status = "failed"
        b.error_message = "The backup did not run (the operation was cancelled or interrupted)."
        b.completed_at = now
        stats["stuck_failed"] += 1
    return stats


def admin_overview(db: Session) -> dict[str, Any]:
    now = utcnow()
    day = now - dt.timedelta(hours=24)
    by_status = dict(db.execute(select(Backup.status, func.count(Backup.id)).group_by(Backup.status)).all())
    stored = db.scalar(select(func.coalesce(func.sum(Backup.size), 0)).where(Backup.status.in_(RESTORABLE))) or 0
    last_day = dict(
        db.execute(
            select(Backup.status, func.count(Backup.id)).where(Backup.created_at >= day).group_by(Backup.status)
        ).all()
    )
    ready = db.scalar(select(func.count(Database.id)).where(Database.status == "ready")) or 0
    covered = (
        db.scalar(
            select(func.count(func.distinct(Backup.database_id)))
            .join(Database, Database.id == Backup.database_id)
            .where(
                Database.status == "ready",
                Backup.status.in_(RESTORABLE),
                Backup.completed_at >= now - dt.timedelta(hours=26),
            )
        )
        or 0
    )
    verification = dict(
        db.execute(
            select(Backup.verification_status, func.count(Backup.id))
            .where(Backup.verification_status.is_not(None), Backup.verified_at >= now - dt.timedelta(days=30))
            .group_by(Backup.verification_status)
        ).all()
    )
    avg_duration = db.scalar(
        select(func.avg(Backup.duration_ms)).where(Backup.completed_at >= now - dt.timedelta(days=7))
    )
    return {
        "by_status": by_status,
        "stored_bytes": int(stored),
        "last_24h": last_day,
        "ready_databases": ready,
        "ready_databases_with_recent_backup": covered,
        "verification_30d": {"passed": verification.get("passed", 0), "failed": verification.get("failed", 0)},
        "avg_duration_ms_7d": int(avg_duration) if avg_duration is not None else None,
    }


def owner_backup_bytes(db: Session, owner_id: uuid.UUID) -> int:
    return int(
        db.scalar(
            select(func.coalesce(func.sum(Backup.size), 0)).where(
                Backup.owner_id == owner_id, Backup.status.in_(RESTORABLE)
            )
        )
        or 0
    )


def list_for_owner_stmt(principal: Principal) -> Any:
    stmt = (
        select(Backup, Database)
        .join(Database, Database.id == Backup.database_id)
        .join(Project, Project.id == Database.project_id)
        .where(Backup.owner_id == principal.user.id, Backup.status != "deleted")
    )
    if principal.project_ids is not None:
        stmt = stmt.where(Database.project_id.in_(principal.project_ids))
    return stmt

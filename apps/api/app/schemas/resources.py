from __future__ import annotations

import datetime as dt
import re
import uuid
from typing import Literal

from pydantic import Field, field_validator

from app.schemas.common import ApiModel, OutModel

Environment = Literal["development", "staging", "production"]
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-]{0,62}[A-Za-z0-9]$|^[A-Za-z0-9]$")
PG_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
RESERVED_PG_NAMES = {"postgres", "template0", "template1"}


def _clean_name(v: str) -> str:
    v = " ".join(v.split())
    if not NAME_RE.match(v):
        raise ValueError("Use 1-64 letters, numbers, spaces, dots, dashes or underscores.")
    return v


# --- projects ---------------------------------------------------------------------------------


class ProjectCreate(ApiModel):
    name: str = Field(min_length=1, max_length=64)
    environment: Environment = "development"
    description: str | None = Field(None, max_length=500)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _clean_name(v)


class ProjectUpdate(ApiModel):
    name: str | None = Field(None, min_length=1, max_length=64)
    environment: Environment | None = None
    description: str | None = Field(None, max_length=500)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        return _clean_name(v) if v is not None else v


class ProjectOut(OutModel):
    id: uuid.UUID
    name: str
    description: str | None
    environment: str
    status: str
    created_at: dt.datetime
    updated_at: dt.datetime
    archived_at: dt.datetime | None
    database_count: int = 0
    allocated_storage_mb: int = 0
    storage_used_bytes: int = 0


# --- databases --------------------------------------------------------------------------------


class DatabaseCreate(ApiModel):
    """Only product-level choices. Images, hosts, volumes, networks and privileges are never
    accepted from clients (extra fields are rejected)."""

    project_id: uuid.UUID
    name: str = Field(min_length=1, max_length=64)
    database_name: str | None = Field(None, max_length=63, description="PostgreSQL database name (defaults from name)")
    postgres_version: str | None = Field(None, max_length=8)
    compute_profile: str = Field("nano", max_length=16)
    storage_mb: int | None = Field(None, ge=256, le=1_048_576)
    environment: Environment | None = None
    region: str | None = Field(None, max_length=64)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _clean_name(v)

    @field_validator("database_name")
    @classmethod
    def _dbname(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not PG_NAME_RE.match(v) or v in RESERVED_PG_NAMES:
            raise ValueError("Use lowercase letters, digits and underscores, starting with a letter.")
        return v


class DatabaseUpdate(ApiModel):
    name: str | None = Field(None, min_length=1, max_length=64)
    environment: Environment | None = None
    max_connections: int | None = Field(None, ge=1, le=500)
    storage_mb: int | None = Field(None, ge=256, le=1_048_576)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        return _clean_name(v) if v is not None else v


class ProvisioningStatus(OutModel):
    stage: str
    step: int
    total_steps: int
    job_id: uuid.UUID | None = None
    job_kind: str | None = None
    job_status: str | None = None
    attempt: int = 0
    max_attempts: int = 0
    error_code: str | None = None
    message: str | None = None
    next_retry_at: dt.datetime | None = None


class DatabaseOut(OutModel):
    id: uuid.UUID
    project_id: uuid.UUID
    project_name: str | None = None
    name: str
    database_name: str
    environment: str
    postgres_version: str
    status: str
    status_reason: str | None
    region: str
    region_label: str | None = None
    compute_profile: str
    cpus: float | None = None
    memory_mb: int | None = None
    allocated_storage_mb: int
    storage_used_bytes: int | None
    storage_over_limit: bool
    max_connections: int
    connections_current: int | None
    host: str
    port: int
    created_at: dt.datetime
    updated_at: dt.datetime
    ready_at: dt.datetime | None
    started_at: dt.datetime | None
    last_activity_at: dt.datetime | None
    metrics_measured_at: dt.datetime | None
    suspended_by_admin: bool = False
    host_status: Literal["ok", "unavailable", "unknown"] = Field(
        "unknown", description="Health of the server the database runs on (the server itself isn't disclosed)"
    )
    source_database_id: uuid.UUID | None = None
    restored_from_backup_id: uuid.UUID | None = None
    data_restored_at: dt.datetime | None = None
    provisioning: ProvisioningStatus | None = None


class ConnectionOut(OutModel):
    host: str
    port: int
    database: str
    username: str
    sslmode: str
    password: str | None = None
    password_revealed: bool
    url: str
    url_masked: str
    credential_id: uuid.UUID | None
    credential_created_at: dt.datetime | None
    ssl_note: str
    rotation_in_progress: bool = False


class DatabaseEventOut(OutModel):
    id: uuid.UUID
    event_type: str
    from_status: str | None
    to_status: str | None
    message: str | None
    actor_type: str
    created_at: dt.datetime


class OptionsOut(OutModel):
    postgres_versions: list[str]
    default_postgres_version: str
    regions: list[dict[str, str]]
    compute_profiles: list[dict[str, object]]
    storage: dict[str, int]
    max_connections_per_db: int
    plan: str


# --- backups & clones -------------------------------------------------------------------------


class BackupOut(OutModel):
    id: uuid.UUID
    database_id: uuid.UUID
    database_name: str | None = None
    database_status: str | None = None
    source_storage_mb: int | None = Field(None, description="Storage a restore of this backup will allocate")
    kind: Literal["scheduled", "manual", "final", "pre_clone", "migration"]
    status: str
    size_bytes: int | None = Field(None, description="Encrypted size as stored")
    table_count: int | None = None
    postgres_version: str | None = None
    created_at: dt.datetime
    started_at: dt.datetime | None = None
    completed_at: dt.datetime | None = None
    duration_ms: int | None = None
    expires_at: dt.datetime | None = None
    verified_at: dt.datetime | None = None
    verification_status: str | None = None
    error_message: str | None = None
    restorable: bool = False


class BackupSummaryOut(OutModel):
    retention_days: int
    manual_backups_per_day: int
    manual_backups_today: int
    schedule: str
    last_backup_at: dt.datetime | None = None
    next_backup_due_at: dt.datetime | None = None
    total_size_bytes: int = 0
    count: int = 0


class CloneCreate(ApiModel):
    """Create a new, independent database from a backup (or from the current state of the source,
    via a fresh backup). Restores never overwrite an existing database."""

    name: str = Field(min_length=1, max_length=64)
    backup_id: uuid.UUID | None = Field(None, description="Restore this backup; omit to copy the current data")
    project_id: uuid.UUID | None = Field(None, description="Defaults to the source database's project")
    environment: Environment | None = None
    compute_profile: str | None = Field(None, max_length=16)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _clean_name(v)

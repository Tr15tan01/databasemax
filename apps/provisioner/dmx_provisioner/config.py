"""Provisioner configuration. Everything infrastructure-related is decided here by the operator;
clients can only pick keys from these allowlists."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


@dataclass(frozen=True)
class ComputeProfile:
    name: str
    cpus: float
    memory_mb: int
    shared_buffers_mb: int
    work_mem_mb: int
    shm_mb: int
    pids_limit: int = 256


PROFILES: dict[str, ComputeProfile] = {
    "nano": ComputeProfile("nano", cpus=0.25, memory_mb=256, shared_buffers_mb=48, work_mem_mb=2, shm_mb=64),
    "small": ComputeProfile("small", cpus=0.5, memory_mb=512, shared_buffers_mb=128, work_mem_mb=4, shm_mb=128),
    "medium": ComputeProfile("medium", cpus=1.0, memory_mb=1024, shared_buffers_mb=256, work_mem_mb=8, shm_mb=256),
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    token: SecretStr = Field(SecretStr("dev-only-provisioner-token"), alias="PROVISIONER_TOKEN")
    admin_secret: SecretStr = Field(SecretStr("dev-only-provisioner-admin-secret"), alias="PROVISIONER_ADMIN_SECRET")
    environment: Literal["development", "test", "production"] = Field("development", alias="DMX_ENV")
    # PostgreSQL major versions offered; each must be installed on the host
    # (/usr/lib/postgresql/<version>/bin, from the postgresql-<version> package).
    pg_versions: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["16"], alias="DMX_PG_VERSIONS")
    host_name: str = Field("local-1", alias="DMX_HOST_NAME")
    region: str = Field("oci-local-1", alias="DMX_REGION")
    data_plane_memory_mb: int = Field(4096, alias="DMX_DATA_PLANE_MEMORY_MB")
    data_plane_disk_mb: int = Field(102400, alias="DMX_DATA_PLANE_DISK_MB")
    data_plane_cpus: float = Field(2.0, alias="DMX_DATA_PLANE_CPUS")
    max_instances: int = Field(200, alias="DMX_MAX_INSTANCES")
    # --- instance networking -----------------------------------------------------------------
    # Each instance listens on this address and a port from the range below. On a single-VM
    # install that's 127.0.0.1 (only the local gateway reaches it). On a worker host use its
    # private (VCN) address and allow only the gateway host in the firewall.
    listen_ip: str = Field("127.0.0.1", alias="DMX_INSTANCE_LISTEN")
    port_min: int = Field(20000, alias="DMX_INSTANCE_PORT_MIN")
    port_max: int = Field(29999, alias="DMX_INSTANCE_PORT_MAX")
    # Where customer roles may log in from: the gateway's address as seen by the instance.
    client_cidrs: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["127.0.0.1/32"], alias="DMX_INSTANCE_CLIENT_CIDRS"
    )
    # --- multi-host --------------------------------------------------------------------------
    # Free-form placement labels reported to the control plane, e.g. {"tier": "standard"}.
    host_labels: dict[str, str] = Field(default_factory=dict, alias="DMX_HOST_LABELS")
    reserved_connections: int = 5
    # "loop": fixed-size ext4 filesystem per instance via the host agent (hard limit).
    # "dir": plain directories (storage limit enforced softly by the control plane).
    # Must match the host agent's DMX_STORAGE_BACKEND (both read /etc/databasemax/.env).
    storage_backend: Literal["loop", "dir"] = Field("dir", alias="DMX_STORAGE_BACKEND")
    agent_socket: str = Field("/run/databasemax/agent.sock", alias="DMX_AGENT_SOCKET")
    # Must match the host agent's DMX_STORAGE_ROOT (instance data lives under <root>/mnt/).
    data_plane_root: str = Field("/var/lib/databasemax/data-plane", alias="DMX_STORAGE_ROOT")
    # Instance metadata (owner, database, port, profile …), one JSON file per instance.
    state_dir: str = Field("/var/lib/databasemax/provisioner", alias="DMX_PROVISIONER_STATE_DIR")

    # --- backups -------------------------------------------------------------------------------
    # Backups are encrypted by the provisioner with keys that no other service holds.
    # DMX_BACKUP_KEYS: JSON {"<key id>": "<base64 32-byte key>"}; the active key encrypts new backups,
    # older keys stay listed so existing backups remain restorable after rotation.
    backup_keys: dict[str, str] = Field(default_factory=dict, alias="DMX_BACKUP_KEYS")
    backup_active_key: str = Field("dev", alias="DMX_BACKUP_ACTIVE_KEY")
    backup_store: Literal["local", "s3"] = Field("local", alias="DMX_BACKUP_STORE")
    backup_dir: str = Field("/var/lib/databasemax/customer-backups", alias="DMX_BACKUP_DIR")
    backup_disk_budget_mb: int = Field(51200, alias="DMX_BACKUP_DISK_BUDGET_MB")
    # S3-compatible object storage (e.g. OCI Object Storage's S3 compatibility API).
    s3_endpoint: str | None = Field(None, alias="DMX_BACKUP_S3_ENDPOINT")
    s3_region: str = Field("us-east-1", alias="DMX_BACKUP_S3_REGION")
    s3_bucket: str | None = Field(None, alias="DMX_BACKUP_S3_BUCKET")
    s3_access_key: SecretStr | None = Field(None, alias="DMX_BACKUP_S3_ACCESS_KEY")
    s3_secret_key: SecretStr | None = Field(None, alias="DMX_BACKUP_S3_SECRET_KEY")
    s3_prefix: str = Field("backups/", alias="DMX_BACKUP_S3_PREFIX")
    backup_timeout_seconds: int = Field(3600, alias="DMX_BACKUP_TIMEOUT_SECONDS")
    verify_profile: str = Field("nano", alias="DMX_VERIFY_PROFILE")

    @field_validator("client_cidrs", mode="before")
    @classmethod
    def _cidrs(cls, v: object) -> object:
        import ipaddress

        items = [x.strip() for x in v.split(",") if x.strip()] if isinstance(v, str) else list(v or [])  # type: ignore[call-overload]
        for c in items:
            ipaddress.ip_network(c)  # raises on invalid input
        return items

    @field_validator("pg_versions", mode="before")
    @classmethod
    def _versions(cls, v: object) -> object:
        import re

        items = [x.strip() for x in v.split(",") if x.strip()] if isinstance(v, str) else list(v or [])  # type: ignore[call-overload]
        for x in items:
            if not re.fullmatch(r"[0-9]{2}", x):
                raise ValueError("PostgreSQL versions are major versions like 16")
        return items

    @field_validator("listen_ip")
    @classmethod
    def _ip(cls, v: str) -> str:
        import ipaddress

        ipaddress.ip_address(v)
        return v

    @field_validator("backup_keys", "host_labels", mode="before")
    @classmethod
    def _json(cls, v: object) -> object:
        if isinstance(v, str):
            return json.loads(v) if v.strip() else {}
        return v


DEV_BACKUP_KEY = hashlib.sha256(b"databasemax-dev-only-backup-key").digest()


def backup_keyring(s: Settings) -> dict[str, bytes]:
    keys: dict[str, bytes] = {}
    for kid, b64 in s.backup_keys.items():
        raw = base64.b64decode(b64)
        if len(raw) != 32:
            raise ValueError(f"backup key {kid!r} must be 32 bytes")
        keys[kid] = raw
    if s.environment != "production":
        keys.setdefault("dev", DEV_BACKUP_KEY)
    return keys


def check_production(s: Settings) -> None:
    """Refuse to start in production with development defaults."""
    if s.environment != "production":
        return
    problems = []
    if s.token.get_secret_value().startswith("dev-only") or len(s.token.get_secret_value()) < 32:
        problems.append("PROVISIONER_TOKEN")
    if s.admin_secret.get_secret_value().startswith("dev-only") or len(s.admin_secret.get_secret_value()) < 32:
        problems.append("PROVISIONER_ADMIN_SECRET")
    keys = backup_keyring(s)
    if s.backup_active_key not in keys or keys[s.backup_active_key] == DEV_BACKUP_KEY:
        problems.append("DMX_BACKUP_KEYS/DMX_BACKUP_ACTIVE_KEY")
    if s.backup_store == "s3" and not (s.s3_bucket and s.s3_endpoint and s.s3_access_key and s.s3_secret_key):
        problems.append("DMX_BACKUP_S3_*")
    if problems:
        raise RuntimeError("insecure or missing provisioner configuration: " + ", ".join(problems))


@lru_cache
def get_settings() -> Settings:
    s = Settings()  # type: ignore[call-arg]
    check_production(s)
    if s.environment != "production" and s.backup_active_key == "dev":
        import logging

        logging.getLogger("dmx.provisioner").warning(
            "backups use the built-in DEVELOPMENT key; set DMX_BACKUP_KEYS for any environment holding real data"
        )
    return s

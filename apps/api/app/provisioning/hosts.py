"""Host registry access: which provisioner to call for a host or a database.

Each registered host runs its own provisioner. Its bearer token is stored encrypted in the
`secrets` table (`infrastructure_hosts.token_secret_reference`). The *bootstrap* host — the
provisioner configured by PROVISIONER_URL / PROVISIONER_TOKEN — needs no stored token.

Databases never move between hosts implicitly: every infrastructure call for a database goes
to the host recorded in `databases.host_id`. Moves happen only through the `migrate` job.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Database, InfrastructureHost, utcnow
from app.provisioning.client import ProvisionerClient
from app.security.secrets_store import secret_store

HEARTBEAT_FRESH = dt.timedelta(minutes=2)


class HostUnavailable(Exception):
    pass


def is_fresh(host: InfrastructureHost, now: dt.datetime | None = None) -> bool:
    return host.last_heartbeat_at is not None and (now or utcnow()) - host.last_heartbeat_at < HEARTBEAT_FRESH


def bootstrap_host(db: Session) -> InfrastructureHost | None:
    return db.scalar(select(InfrastructureHost).where(InfrastructureHost.endpoint == get_settings().provisioner_url))


class ProvisionerPool:
    """Hands out a ProvisionerClient per host. `override` (tests) is used for every host."""

    def __init__(self, override: ProvisionerClient | None = None, timeout: float = 120.0):
        self.override = override
        self.timeout = timeout
        self._cache: dict[tuple[Any, ...], ProvisionerClient] = {}

    def default(self) -> ProvisionerClient:
        if self.override is not None:
            return self.override
        key = ("default",)
        if key not in self._cache:
            self._cache[key] = ProvisionerClient(timeout=self.timeout)
        return self._cache[key]

    def for_host(self, db: Session, host: InfrastructureHost | None) -> ProvisionerClient:
        if self.override is not None:
            return self.override
        if host is None:
            return self.default()
        key = (host.id, host.endpoint, host.token_secret_reference)
        if key in self._cache:
            return self._cache[key]
        s = get_settings()
        if host.token_secret_reference is not None:
            token = secret_store.get(db, host.token_secret_reference)
        elif host.endpoint == s.provisioner_url:
            token = s.provisioner_token.get_secret_value()
        else:
            raise HostUnavailable(f"host {host.name} has no provisioner token registered")
        client = ProvisionerClient(base_url=host.endpoint, token=token, timeout=self.timeout)
        self._cache[key] = client
        return client

    def for_host_id(self, db: Session, host_id: uuid.UUID | None) -> ProvisionerClient:
        return self.for_host(db, db.get(InfrastructureHost, host_id) if host_id else None)

    def for_database(self, db: Session, database: Database) -> ProvisionerClient:
        return self.for_host_id(db, database.host_id)


def register_host(
    db: Session,
    *,
    name: str,
    endpoint: str,
    region: str,
    token: str,
    labels: dict[str, str] | None = None,
    provider: str = "systemd",
) -> InfrastructureHost:
    """Create or update a host entry (token stored encrypted). Caller verifies reachability."""
    host = db.scalar(select(InfrastructureHost).where(InfrastructureHost.name == name))
    if host is None:
        host = InfrastructureHost(
            id=uuid.uuid4(),
            name=name,
            provider=provider,
            region=region,
            endpoint=endpoint,
            status="active",
            capacity_metadata={},
            labels=labels or {},
            status_changed_at=utcnow(),
        )
        db.add(host)
        db.flush()
    else:
        host.endpoint = endpoint
        host.region = region
        if labels is not None:
            host.labels = labels
    old = host.token_secret_reference
    host.token_secret_reference = secret_store.put(db, token, purpose="provisioner_token")
    if old is not None:
        secret_store.destroy(db, old)
    return host

"""Provisioner HTTP API (private network only).

A closed set of operations; every request is authenticated with a shared bearer token and
every field is validated. There is deliberately no endpoint that accepts a binary, a path, a
mount, a network, a capability, an environment variable or a command.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import sys
import tempfile
import threading
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from dmx_provisioner import backups, host_agent, pgadmin
from dmx_provisioner import instances as backend
from dmx_provisioner.backup_store import get_store, object_key
from dmx_provisioner.config import get_settings
from dmx_provisioner.validation import ValidationFailed

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format='{"level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
)
log = logging.getLogger("dmx.provisioner")

app = FastAPI(title="DatabaseMax provisioner", docs_url=None, redoc_url=None, openapi_url=None)

_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
_locks_guard = threading.Lock()


@contextmanager
def instance_lock(iid: str) -> Iterator[None]:
    with _locks_guard:
        lock = _locks[iid]
    if not lock.acquire(timeout=120):
        raise HTTPException(409, "instance busy")
    try:
        yield
    finally:
        lock.release()


def auth(authorization: str = Header(default="")) -> None:
    expected = f"Bearer {get_settings().token.get_secret_value()}"
    if not hmac.compare_digest(authorization.encode(), expected.encode()):
        raise HTTPException(401, "unauthorized")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EnsureIn(Strict):
    pg_version: str = Field(max_length=8)
    profile: str = Field(max_length=32)
    storage_mb: int
    max_connections: int
    database_id: str = Field(max_length=64)
    owner_id: str = Field(max_length=64)


class ConfigureIn(Strict):
    database: str = Field(max_length=63)
    username: str = Field(max_length=63)
    scram_verifier: str = Field(max_length=200)
    connection_limit: int


class RotateIn(Strict):
    username: str = Field(max_length=63)
    scram_verifier: str = Field(max_length=200)
    terminate_sessions: bool = True


class StorageIn(Strict):
    storage_mb: int


class LimitIn(Strict):
    username: str = Field(max_length=63)
    connection_limit: int


class ReadOnlyIn(Strict):
    database: str = Field(max_length=63)
    read_only: bool


UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"


class BackupIn(Strict):
    database: str = Field(max_length=63)
    database_id: str = Field(pattern=UUID_PATTERN)
    backup_id: str = Field(pattern=UUID_PATTERN)


class RestoreIn(Strict):
    database: str = Field(max_length=63)
    username: str = Field(max_length=63)
    source_database_id: str = Field(pattern=UUID_PATTERN)
    backup_id: str = Field(pattern=UUID_PATTERN)
    sha256: str = Field(pattern=SHA256_PATTERN)


class VerifyIn(Strict):
    source_database_id: str = Field(pattern=UUID_PATTERN)
    backup_id: str = Field(pattern=UUID_PATTERN)
    sha256: str = Field(pattern=SHA256_PATTERN)
    pg_version: str = Field(max_length=8)
    expected_table_count: int | None = Field(default=None, ge=0)
    storage_mb: int = Field(default=1024, ge=64, le=1024 * 1024)


def _out(i: backend.InstanceInfo) -> dict[str, Any]:
    return {
        "instance_id": i.instance_id,
        "state": i.state,
        "endpoint_host": i.endpoint_host,
        "endpoint_port": i.endpoint_port,
        "database_id": i.database_id,
        "owner_id": i.owner_id,
        "pg_version": i.pg_version,
        "profile": i.profile,
        "storage_mb": i.storage_mb,
        "created": i.created,
        "started_at": i.started_at,
        "purpose": i.purpose,
    }


@app.exception_handler(ValidationFailed)
async def _validation(_: Request, exc: ValidationFailed) -> JSONResponse:
    return JSONResponse({"code": "invalid_request", "message": str(exc)}, status_code=422)


@app.exception_handler(backend.CapacityExceeded)
async def _capacity(_: Request, exc: backend.CapacityExceeded) -> JSONResponse:
    return JSONResponse({"code": "capacity_exceeded", "message": str(exc)}, status_code=409)


@app.exception_handler(backend.InstanceNotFound)
async def _nf(_: Request, exc: backend.InstanceNotFound) -> JSONResponse:
    return JSONResponse({"code": "not_found", "message": "instance not found"}, status_code=404)


@app.exception_handler(backups.BackupFailed)
async def _backup_failed(_: Request, exc: backups.BackupFailed) -> JSONResponse:
    status = {"not_found": 404, "backup_missing": 404, "not_running": 409, "target_not_empty": 409,
              "capacity_exceeded": 409, "integrity_error": 422}.get(exc.code, 500)  # fmt: skip
    log.warning("backup operation failed: %s", exc.code)
    return JSONResponse({"code": exc.code, "message": exc.message}, status_code=status)


@app.exception_handler(host_agent.HostAgentError)
async def _agent(_: Request, exc: host_agent.HostAgentError) -> JSONResponse:
    log.error("host agent error: %s", exc.code)
    status = (409 if exc.code in ("shrink_not_supported", "volume_in_use") else 503 if exc.code == "agent_unreachable"
              else 422 if exc.status == 422 else 500)  # fmt: skip
    return JSONResponse({"code": f"host_{exc.code}", "message": exc.message}, status_code=status)


@app.exception_handler(Exception)
async def _any(_: Request, exc: Exception) -> JSONResponse:
    log.error("operation failed: %s", type(exc).__name__)
    return JSONResponse({"code": "provisioner_error", "message": type(exc).__name__}, status_code=500)


@app.get("/v1/health")
def health() -> dict[str, Any]:
    host_agent.health()
    return {"status": "ok"}


@app.get("/v1/capacity", dependencies=[Depends(auth)])
def get_capacity() -> dict[str, Any]:
    return {**backend.capacity(), "backups": backups.usage()}


@app.get("/v1/instances", dependencies=[Depends(auth)])
def list_instances() -> list[dict[str, Any]]:
    return [_out(i) for i in backend.list_instances()]


@app.get("/v1/instances/{iid}", dependencies=[Depends(auth)])
def get_instance(iid: str) -> dict[str, Any]:
    i = backend.get_instance(iid)
    if i is None:
        raise HTTPException(404, "instance not found")
    return _out(i)


@app.put("/v1/instances/{iid}", dependencies=[Depends(auth)])
def ensure_instance(iid: str, body: EnsureIn) -> dict[str, Any]:
    with instance_lock(iid):
        i = backend.ensure_instance(
            iid,
            version=body.pg_version,
            profile_name=body.profile,
            storage_mb=body.storage_mb,
            max_connections=body.max_connections,
            database_id=body.database_id,
            owner_id=body.owner_id,
        )
    return _out(i)


def _mgmt_host(iid: str) -> str:
    i = backend.get_instance(iid)
    if i is None:
        raise HTTPException(404, "instance not found")
    if i.state != "running" or not i.mgmt_host:
        raise HTTPException(409, "instance not running")
    return i.mgmt_host


@app.post("/v1/instances/{iid}/configure", dependencies=[Depends(auth)])
def configure(iid: str, body: ConfigureIn) -> dict[str, str]:
    with instance_lock(iid):
        info = backend.get_instance(iid)
        pgadmin.configure(
            iid,
            _mgmt_host(iid),
            database=body.database,
            username=body.username,
            scram_verifier=body.scram_verifier,
            connection_limit=body.connection_limit,
            tablespace=info.tablespace if info else None,
        )
    return {"status": "configured"}


@app.post("/v1/instances/{iid}/rotate", dependencies=[Depends(auth)])
def rotate(iid: str, body: RotateIn) -> dict[str, Any]:
    with instance_lock(iid):
        n = pgadmin.rotate(
            iid,
            _mgmt_host(iid),
            username=body.username,
            scram_verifier=body.scram_verifier,
            terminate_sessions=body.terminate_sessions,
        )
    return {"status": "rotated", "terminated_sessions": n}


class TerminateIn(Strict):
    username: str = Field(max_length=63)


@app.post("/v1/instances/{iid}/terminate-sessions", dependencies=[Depends(auth)])
def terminate_sessions(iid: str, body: TerminateIn) -> dict[str, Any]:
    return {"terminated": pgadmin.terminate_sessions(iid, _mgmt_host(iid), username=body.username)}


@app.post("/v1/instances/{iid}/connection-limit", dependencies=[Depends(auth)])
def connection_limit(iid: str, body: LimitIn) -> dict[str, str]:
    with instance_lock(iid):
        pgadmin.set_connection_limit(
            iid, _mgmt_host(iid), username=body.username, connection_limit=body.connection_limit
        )
    return {"status": "updated"}


@app.post("/v1/instances/{iid}/storage", dependencies=[Depends(auth)])
def storage(iid: str, body: StorageIn) -> dict[str, Any]:
    with instance_lock(iid):
        return backend.resize_storage(iid, body.storage_mb)


@app.post("/v1/instances/{iid}/read-only", dependencies=[Depends(auth)])
def read_only(iid: str, body: ReadOnlyIn) -> dict[str, str]:
    with instance_lock(iid):
        pgadmin.set_read_only(iid, _mgmt_host(iid), database=body.database, read_only=body.read_only)
    return {"status": "updated"}


@app.post("/v1/instances/{iid}/stop", dependencies=[Depends(auth)])
def stop(iid: str) -> dict[str, str]:
    with instance_lock(iid):
        backend.stop_instance(iid)
    return {"status": "stopped"}


@app.post("/v1/instances/{iid}/start", dependencies=[Depends(auth)])
def start(iid: str) -> dict[str, Any]:
    with instance_lock(iid):
        i = backend.start_instance(iid)
    return _out(i)


@app.delete("/v1/instances/{iid}", dependencies=[Depends(auth)])
def destroy(iid: str) -> dict[str, Any]:
    with instance_lock(iid):
        removed = backend.destroy_instance(iid)
    return {"status": "destroyed", "removed": removed}


@app.get("/v1/instances/{iid}/stats", dependencies=[Depends(auth)])
def stats(iid: str, database: str, username: str) -> dict[str, Any]:
    i = backend.get_instance(iid)
    if i is None:
        raise HTTPException(404, "instance not found")
    out: dict[str, Any] = {"state": i.state, "measured": {}}
    if i.state == "running" and i.mgmt_host:
        out["measured"].update(pgadmin.stats(iid, i.mgmt_host, database=database, username=username))
        out["measured"].update(backend.resource_stats(iid))
        out["measured"].update(backend.volume_usage(iid))
    return out


@app.get("/v1/instances/{iid}/live", dependencies=[Depends(auth)])
def live(iid: str, database: str, username: str) -> dict[str, Any]:
    """Point-in-time load sample for the dashboard's live monitor."""
    i = backend.get_instance(iid)
    if i is None:
        raise HTTPException(404, "instance not found")
    out: dict[str, Any] = {"state": i.state, "measured": {}}
    if i.state == "running" and i.mgmt_host:
        out["measured"].update(pgadmin.live(iid, i.mgmt_host, database=database, username=username))
        out["measured"].update(backend.resource_stats(iid))
    return out


# --- backups ------------------------------------------------------------------------------------


@app.post("/v1/instances/{iid}/backups", dependencies=[Depends(auth)])
def create_backup(iid: str, body: BackupIn) -> dict[str, Any]:
    # No instance lock: a backup must never block stop/delete/suspend. If the instance stops
    # mid-dump the dump fails and is retried by the control plane.
    r = backups.create_backup(iid, database=body.database, database_id=body.database_id, backup_id=body.backup_id)
    log.info("backup created: %s bytes", r.size)
    return r.__dict__


@app.post("/v1/instances/{iid}/restore", dependencies=[Depends(auth)])
def restore_backup(iid: str, body: RestoreIn) -> dict[str, Any]:
    with instance_lock(iid):
        return backups.restore_backup(
            iid,
            database=body.database,
            username=body.username,
            source_database_id=body.source_database_id,
            backup_id=body.backup_id,
            sha256=body.sha256,
        )


@app.post("/v1/backups/verify", dependencies=[Depends(auth)])
def verify_backup(body: VerifyIn) -> dict[str, Any]:
    if body.pg_version not in get_settings().pg_versions:
        raise HTTPException(422, "unsupported PostgreSQL version")
    return backups.verify_backup(
        source_database_id=body.source_database_id,
        backup_id=body.backup_id,
        sha256=body.sha256,
        pg_version=body.pg_version,
        expected_tables=body.expected_table_count,
        storage_mb=body.storage_mb,
    )


@app.delete("/v1/backups/{database_id}/{backup_id}", dependencies=[Depends(auth)])
def delete_backup(database_id: str, backup_id: str) -> dict[str, Any]:
    return {"status": "deleted", "existed": backups.delete_backup(database_id, backup_id)}


@app.post("/v1/maintenance/orphan-volumes", dependencies=[Depends(auth)])
def orphan_volumes() -> dict[str, Any]:
    removed = backend.cleanup_orphan_volumes()
    stale_verify = backend.cleanup_stale_verify_instances()
    return {"removed_volumes": removed, "removed_verify_instances": stale_verify}


# --- backup object relay (multi-host) -------------------------------------------------------------
# The control plane copies ciphertext between hosts whose stores aren't shared (local stores).
# Only encrypted bytes ever move; keys stay on the provisioners.


@app.get("/v1/backups/{database_id}/{backup_id}/object", dependencies=[Depends(auth)])
def get_backup_object(database_id: str, backup_id: str) -> StreamingResponse:
    key = object_key(database_id, backup_id)
    try:
        f = get_store().open(key)
    except FileNotFoundError as e:
        raise HTTPException(404, "backup object not found") from e

    def chunks() -> Iterator[bytes]:
        try:
            while True:
                b = f.read(1024 * 1024)
                if not b:
                    return
                yield b
        finally:
            f.close()

    return StreamingResponse(chunks(), media_type="application/octet-stream")


@app.put("/v1/backups/{database_id}/{backup_id}/object", dependencies=[Depends(auth)])
async def put_backup_object(database_id: str, backup_id: str, request: Request, sha256: str) -> dict[str, Any]:
    key = object_key(database_id, backup_id)
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise HTTPException(422, "invalid checksum")
    spool = tempfile.TemporaryFile(dir=backups._spool_dir())  # noqa: SIM115 - closed below
    h = hashlib.sha256()
    size = 0
    try:
        async for chunk in request.stream():
            spool.write(chunk)
            h.update(chunk)
            size += len(chunk)
        if not secrets.compare_digest(h.hexdigest(), sha256):
            raise HTTPException(422, "checksum mismatch")
        spool.seek(0)

        def read_all() -> Iterator[bytes]:
            while b := spool.read(1024 * 1024):
                yield b

        obj = await run_in_threadpool(get_store().put, key, read_all())
    finally:
        spool.close()
    return {"size": obj.size, "sha256": obj.sha256, "storage_reference": obj.reference}

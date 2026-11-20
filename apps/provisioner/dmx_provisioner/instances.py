"""Customer PostgreSQL instances as systemd units.

Each instance is a separate PostgreSQL server (`dmx-pg@<iid>.service`) running the
distribution's binaries as its own system user, with cgroup limits from its compute profile, a
filesystem sandbox that shows it only its own data, no outbound network and its own port on a
private address. Everything that needs root (system users, filesystems, unit files) is done by
the host agent through a closed API; this module decides *what* to run and keeps the metadata.

Nothing about binaries, paths, users, limits or network comes from the client: versions and
profiles are picked from allowlists, ports from a fixed range.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import logging
import os
import platform
import socket
import subprocess  # nosec B404 - fixed argv only, never a shell
import threading
import time
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from dmx_provisioner import host_agent
from dmx_provisioner.config import PROFILES, ComputeProfile, get_settings
from dmx_provisioner.scram import admin_password, verifier
from dmx_provisioner.validation import ValidationFailed
from dmx_provisioner.validation import instance_id as check_instance_id

log = logging.getLogger("dmx.provisioner.instances")

TABLESPACE = "dmx_data"


class ExecFailed(Exception):
    def __init__(self, program: str, exit_code: int | None, detail: str):
        super().__init__(f"{program} exited with {exit_code}")
        self.program = program
        self.exit_code = exit_code
        self.detail = detail


class CapacityExceeded(Exception):
    pass


class InstanceNotFound(Exception):
    pass


@dataclass
class InstanceInfo:
    instance_id: str
    state: str  # running | starting | stopping | stopped | failed | missing
    endpoint_host: str | None
    endpoint_port: int
    mgmt_host: str | None
    database_id: str | None
    owner_id: str | None
    pg_version: str | None
    profile: str | None
    storage_mb: int
    created: str | None
    started_at: str | None
    health: str | None
    purpose: str = "customer"
    tablespace: str | None = None


@dataclass
class Meta:
    instance_id: str
    database_id: str
    owner_id: str
    pg_version: str
    profile: str
    storage_mb: int
    max_connections: int
    port: int
    purpose: str
    storage_backend: str
    host: str
    created: str


# --- metadata -------------------------------------------------------------------------------------


def _meta_dir() -> Path:
    d = Path(get_settings().state_dir) / "instances"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def load_meta(iid: str) -> Meta | None:
    f = _meta_dir() / f"{iid}.json"
    try:
        return Meta(**json.loads(f.read_text()))
    except FileNotFoundError:
        return None


def _save_meta(m: Meta) -> None:
    f = _meta_dir() / f"{m.instance_id}.json"
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(m)))
    os.chmod(tmp, 0o600)
    os.replace(tmp, f)


def _delete_meta(iid: str) -> None:
    (_meta_dir() / f"{iid}.json").unlink(missing_ok=True)


def _all_meta() -> list[Meta]:
    out = []
    for f in sorted(_meta_dir().glob("*.json")):
        with contextlib.suppress(ValueError, TypeError):
            out.append(Meta(**json.loads(f.read_text())))
    return out


# --- naming and paths -----------------------------------------------------------------------------


def pg_bin(version: str, program: str) -> str:
    if version not in get_settings().pg_versions:
        raise ValidationFailed("unsupported PostgreSQL version")
    if program not in ("pg_dump", "pg_restore", "psql"):
        raise ValidationFailed("unsupported program")
    return f"/usr/lib/postgresql/{version}/bin/{program}"


def tablespace_location(iid: str) -> str:
    check_instance_id(iid)
    return f"{get_settings().data_plane_root.rstrip('/')}/mnt/{iid}/data"


def admin_port(iid: str) -> int:
    m = load_meta(iid)
    if m is None:
        raise InstanceNotFound(iid)
    return m.port


def admin_env(iid: str) -> dict[str, str]:
    """libpq environment for the instance's admin role (password derived per instance)."""
    return {
        "PGPASSWORD": admin_password(get_settings().admin_secret.get_secret_value(), iid),
        "PGSSLMODE": "disable",  # loopback only; TLS terminates at the gateway
        "PGCONNECT_TIMEOUT": "10",
    }


_STATES = {"active": "running", "activating": "starting", "reloading": "running",
           "deactivating": "stopping", "inactive": "stopped", "failed": "failed"}  # fmt: skip


def _info(m: Meta, st: dict[str, Any] | None) -> InstanceInfo:
    s = get_settings()
    state = _STATES.get((st or {}).get("active_state", ""), "missing") if st and st.get("exists") else "missing"
    return InstanceInfo(
        instance_id=m.instance_id,
        state=state,
        endpoint_host=s.listen_ip,
        endpoint_port=m.port,
        mgmt_host="127.0.0.1",
        database_id=m.database_id,
        owner_id=m.owner_id,
        pg_version=m.pg_version,
        profile=m.profile,
        storage_mb=m.storage_mb,
        created=m.created,
        started_at=(st or {}).get("started_at") or None,
        health="healthy" if state == "running" else None,
        purpose=m.purpose,
        tablespace=TABLESPACE,
    )


def get_instance(iid: str) -> InstanceInfo | None:
    check_instance_id(iid)
    m = load_meta(iid)
    if m is None:
        return None
    return _info(m, host_agent.instance(iid))


def list_instances() -> list[InstanceInfo]:
    states = {st["instance_id"]: st for st in host_agent.list_instances()}
    return [_info(m, states.get(m.instance_id)) for m in _all_meta()]


# --- capacity -------------------------------------------------------------------------------------


def wal_limit_mb(storage_mb: int) -> int:
    """max_wal_size scaled to the allocation, so WAL fits in the filesystem headroom."""
    return max(64, min(1024, storage_mb // 8))


def volume_size_mb(storage_mb: int) -> int:
    """Tablespace filesystem: the allocation (as measured by pg_database_size) plus
    filesystem/catalog overhead. WAL and cluster files live on their own filesystems."""
    return storage_mb + 64 + storage_mb // 20


def wal_volume_size_mb(storage_mb: int) -> int:
    """max_wal_size is only a checkpoint trigger: during a fast bulk load WAL can run well past it
    before the checkpoint completes, and running out of WAL space PANICs the server — so leave
    generous room (the image is sparse on the host)."""
    return max(320, 3 * wal_limit_mb(storage_mb) + 128)


SYS_VOLUME_MB = 384  # PGDATA: cluster-wide files (pg_xact, pg_logical, global, `postgres` db)


def host_disk_mb(storage_mb: int) -> int:
    return volume_size_mb(storage_mb) + wal_volume_size_mb(storage_mb) + SYS_VOLUME_MB


def _meminfo_mb() -> int | None:
    with contextlib.suppress(OSError, ValueError, IndexError):
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) // 1024
    return None


def capacity() -> dict[str, Any]:
    s = get_settings()
    metas = _all_meta()
    try:
        states = {st["instance_id"]: st for st in host_agent.list_instances()}
        sizes: dict[str, int] = {}
        for v in host_agent.list_volumes():
            base = v["instance_id"].removesuffix("-wal").removesuffix("-sys")
            sizes[base] = sizes.get(base, 0) + int(v["image_size_mb"])
        storage: dict[str, Any] = {"backend": s.storage_backend, "hard_limits": s.storage_backend == "loop",
                                   "agent": "ok", "data_plane_fs": host_agent.host()}  # fmt: skip
    except host_agent.HostAgentError:
        states, sizes = {}, {}
        storage = {"backend": s.storage_backend, "hard_limits": s.storage_backend == "loop", "agent": "unreachable"}
    mem, cpus, disk, running = 0, 0.0, 0, 0
    for m in metas:
        p = PROFILES.get(m.profile)
        if p:
            mem += p.memory_mb
            cpus += p.cpus
        disk += sizes.pop(m.instance_id, host_disk_mb(m.storage_mb))
        running += (states.get(m.instance_id) or {}).get("active_state") == "active"
    disk += sum(sizes.values())  # volumes without an instance still occupy the host
    return {
        "host_name": s.host_name,
        "region": s.region,
        "instances": len(metas),
        "running": running,
        "max_instances": s.max_instances,
        "allocated_memory_mb": mem,
        "memory_budget_mb": s.data_plane_memory_mb,
        "allocated_cpus": round(cpus, 2),
        "cpu_budget": s.data_plane_cpus,
        "allocated_disk_mb": disk,
        "disk_budget_mb": s.data_plane_disk_mb,
        "versions": sorted(s.pg_versions),
        "storage_backend": s.storage_backend,
        "labels": s.host_labels,
        "runtime": "systemd",
        "listen_ip": s.listen_ip,
        "port_min": s.port_min,
        "port_max": s.port_max,
        "storage": storage,
        "profiles": {k: {"cpus": v.cpus, "memory_mb": v.memory_mb} for k, v in PROFILES.items()},
        "host_cpus": os.cpu_count(),
        "host_memory_mb": _meminfo_mb(),
        "kernel": platform.release(),
        "os": platform.platform(terse=True),
    }


def _check_capacity(profile: ComputeProfile, storage_mb: int) -> None:
    cap = capacity()
    s = get_settings()
    if cap["instances"] + 1 > s.max_instances:
        raise CapacityExceeded("instance limit reached on this host")
    if cap["allocated_memory_mb"] + profile.memory_mb > s.data_plane_memory_mb:
        raise CapacityExceeded("data-plane memory budget exhausted")
    if cap["allocated_disk_mb"] + host_disk_mb(storage_mb) > s.data_plane_disk_mb:
        raise CapacityExceeded("data-plane disk budget exhausted")


_port_lock = threading.Lock()


def _port_free(port: int) -> bool:
    s = get_settings()
    for addr in {"127.0.0.1", s.listen_ip}:
        with socket.socket(socket.AF_INET6 if ":" in addr else socket.AF_INET) as sock:
            try:
                sock.bind((addr, port))
            except OSError:
                return False
    return True


def _allocate_port() -> int:
    s = get_settings()
    used = {m.port for m in _all_meta()}
    for port in range(s.port_min, s.port_max + 1):
        if port not in used and _port_free(port):
            return port
    raise CapacityExceeded("no free instance ports on this host")


# --- lifecycle ------------------------------------------------------------------------------------


def _spec(m: Meta, profile: ComputeProfile) -> dict[str, Any]:
    s = get_settings()
    return {
        "version": m.pg_version,
        "port": m.port,
        "listen": s.listen_ip,
        # Scratch verification instances accept no customer logins at all.
        "client_cidrs": list(s.client_cidrs) if m.purpose == "customer" else [],
        "memory_mb": profile.memory_mb,
        "cpus": profile.cpus,
        "tasks": profile.pids_limit,
        "settings": {
            "max_connections": m.max_connections + s.reserved_connections,
            "shared_buffers_mb": profile.shared_buffers_mb,
            "work_mem_mb": profile.work_mem_mb,
            "effective_cache_size_mb": max(16, int(profile.memory_mb * 0.6)),
            "maintenance_work_mem_mb": max(16, profile.memory_mb // 16),
            "max_wal_size_mb": wal_limit_mb(m.storage_mb),
        },
        "admin_scram": verifier(admin_password(s.admin_secret.get_secret_value(), m.instance_id)),
        "volumes": {
            "sys": SYS_VOLUME_MB,
            "wal": wal_volume_size_mb(m.storage_mb),
            "data": volume_size_mb(m.storage_mb),
        },
    }


def ensure_instance(
    iid: str,
    *,
    version: str,
    profile_name: str,
    storage_mb: int,
    max_connections: int,
    database_id: str,
    owner_id: str,
    purpose: str = "customer",
) -> InstanceInfo:
    """Idempotent: returns the existing instance if already created, starting it if needed."""
    s = get_settings()
    check_instance_id(iid)
    if version not in s.pg_versions:
        raise ValidationFailed("unsupported PostgreSQL version")
    profile = PROFILES.get(profile_name)
    if profile is None:
        raise ValidationFailed("unknown compute profile")
    if not (1 <= max_connections <= 500) or not (64 <= storage_mb <= 1024 * 1024):
        raise ValidationFailed("limits out of range")

    m = load_meta(iid)
    if m is not None:
        if m.database_id != database_id:
            raise ValidationFailed("instance id already belongs to another database")
        st = host_agent.instance(iid)
        if st is None:  # creation was interrupted: finish it
            host_agent.ensure_instance(iid, _spec(m, profile))
        elif st.get("active_state") != "active":
            host_agent.start(iid, admin_scram=_spec(m, profile)["admin_scram"])
        return wait_ready(iid)

    _check_capacity(profile, storage_mb)
    with _port_lock:
        m = Meta(
            instance_id=iid,
            database_id=database_id,
            owner_id=owner_id,
            pg_version=version,
            profile=profile.name,
            storage_mb=storage_mb,
            max_connections=max_connections,
            port=_allocate_port(),
            purpose=purpose,
            storage_backend=s.storage_backend,
            host=s.host_name,
            created=dt.datetime.now(dt.UTC).isoformat(),
        )
        _save_meta(m)  # reserves the port before the (slow) agent call
    try:
        host_agent.ensure_instance(iid, _spec(m, profile))
    except Exception:
        with contextlib.suppress(Exception):
            host_agent.destroy(iid)
        _delete_meta(iid)
        raise
    return wait_ready(iid)


def wait_ready(iid: str, timeout: float = 60.0) -> InstanceInfo:
    """Wait until PostgreSQL accepts admin connections."""
    from dmx_provisioner import pgadmin

    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        i = get_instance(iid)
        if i is None:
            raise InstanceNotFound(iid)
        if i.state == "failed":
            raise RuntimeError("instance failed during startup")
        if i.state == "running" and i.mgmt_host:
            try:
                pgadmin.ping(iid, i.mgmt_host)
                return i
            except Exception as e:  # not ready yet
                last_err = e
        time.sleep(0.5)
    raise TimeoutError(f"instance not ready within {timeout}s: {type(last_err).__name__ if last_err else ''}")


def stop_instance(iid: str) -> None:
    check_instance_id(iid)
    if load_meta(iid) is not None:
        host_agent.stop(iid)


def start_instance(iid: str) -> InstanceInfo:
    check_instance_id(iid)
    m = load_meta(iid)
    if m is None:
        raise InstanceNotFound(iid)
    host_agent.start(iid, admin_scram=verifier(admin_password(get_settings().admin_secret.get_secret_value(), iid)))
    return wait_ready(iid)


def destroy_instance(iid: str) -> bool:
    """Idempotent removal of the unit, its filesystems and system user. True if anything was removed."""
    check_instance_id(iid)
    removed = bool(host_agent.destroy(iid).get("removed"))
    if load_meta(iid) is not None:
        _delete_meta(iid)
        removed = True
    return removed


def instance_storage_backend(iid: str) -> str:
    m = load_meta(iid)
    return m.storage_backend if m else get_settings().storage_backend


def resize_storage(iid: str, storage_mb: int) -> dict[str, Any]:
    """Apply a new allocation. Hard-limited volumes grow online; shrinking keeps the larger
    filesystem (the control plane's soft limit enforces the lower allocation)."""
    check_instance_id(iid)
    if not 64 <= storage_mb <= 1024 * 1024:
        raise ValidationFailed("storage out of range")
    m = load_meta(iid)
    if m is None:
        raise InstanceNotFound(iid)
    current = host_agent.get_volume(iid)
    if current is None:
        raise InstanceNotFound(iid)
    target = volume_size_mb(storage_mb)
    hard = m.storage_backend == "loop"
    if target > current["image_size_mb"]:
        cap = capacity()
        if cap["allocated_disk_mb"] + (target - current["image_size_mb"]) > get_settings().data_plane_disk_mb:
            raise CapacityExceeded("data-plane disk budget exhausted")
    m.storage_mb = storage_mb
    _save_meta(m)
    if target <= current["image_size_mb"]:
        return {"backend": m.storage_backend, "hard_limit": hard, "changed": False,
                "fs_size_bytes": current.get("fs_size_bytes")}  # fmt: skip
    mode = "online"
    try:
        info = host_agent.grow(iid, size_mb=target)
    except host_agent.HostAgentError as e:
        if e.code != "online_resize_unavailable":
            raise
        # Kernel/host doesn't allow online ext4 resize: brief stop → offline grow → start.
        st = host_agent.instance(iid)
        was_running = bool(st and st.get("active_state") == "active")
        if was_running:
            host_agent.stop(iid)
        try:
            info = host_agent.grow(iid, size_mb=target, offline=True)
        finally:
            if was_running:
                start_instance(iid)
        mode = "offline"
    return {"backend": m.storage_backend, "hard_limit": hard, "changed": True, "mode": mode,
            "fs_size_bytes": info.get("fs_size_bytes")}  # fmt: skip


def volume_usage(iid: str) -> dict[str, Any]:
    if instance_storage_backend(iid) != "loop":
        return {}
    v = host_agent.get_volume(iid)
    if not v or not v.get("mounted"):
        return {}
    return {"fs_size_bytes": v["fs_size_bytes"], "fs_used_bytes": v["fs_used_bytes"]}


# --- resource usage (cgroup v2) -------------------------------------------------------------------


def _read_int(p: Path) -> int | None:
    try:
        v = p.read_text().strip()
    except OSError:
        return None
    return None if v == "max" else int(v)


def _cpu_usec(cg: Path) -> int | None:
    with contextlib.suppress(OSError, ValueError, IndexError):
        for line in (cg / "cpu.stat").read_text().splitlines():
            if line.startswith("usage_usec "):
                return int(line.split()[1])
    return None


def resource_stats(iid: str, sample_seconds: float = 0.5) -> dict[str, Any]:
    """Memory (excluding reclaimable page cache), CPU over a short sample — as a percentage of
    one CPU — and process count, read from the unit's cgroup."""
    check_instance_id(iid)
    st = host_agent.instance(iid)
    if not st or st.get("active_state") != "active" or not st.get("control_group"):
        return {}
    cg = Path("/sys/fs/cgroup") / str(st["control_group"]).lstrip("/")
    u0, t0 = _cpu_usec(cg), time.monotonic()
    time.sleep(sample_seconds)
    u1, t1 = _cpu_usec(cg), time.monotonic()
    usage = _read_int(cg / "memory.current")
    inactive = 0
    with contextlib.suppress(OSError, ValueError):
        for line in (cg / "memory.stat").read_text().splitlines():
            if line.startswith("inactive_file "):
                inactive = int(line.split()[1])
    cpu = round((u1 - u0) / ((t1 - t0) * 1e6) * 100, 2) if u0 is not None and u1 is not None and u1 >= u0 else None
    return {
        "memory_usage_bytes": usage - inactive if usage is not None else None,
        "memory_limit_bytes": _read_int(cg / "memory.max"),
        "cpu_percent": cpu,
        "pids": _read_int(cg / "pids.current"),
    }


# --- running PostgreSQL client programs (pg_dump / pg_restore) ------------------------------------

_EXEC_ERR_LIMIT = 4000


def _program(argv: list[str]) -> str:
    """The real program name when argv is wrapped in `timeout …`."""
    if argv and argv[0] == "timeout":
        prog = next((a for a in argv[1:] if not a.startswith("-") and not a.isdigit()), "timeout")
        return Path(prog).name
    return Path(argv[0]).name if argv else "?"


def _safe_detail(err: bytes) -> str:
    # Keep the last line only (the actual error), never full logs.
    lines = [ln for ln in err.decode("utf-8", "replace").splitlines() if ln.strip()]
    return (lines[-1] if lines else "")[:300]


def _env(extra: dict[str, str] | None) -> dict[str, str]:
    # A minimal environment: nothing from the provisioner's own configuration leaks to children.
    return {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", **(extra or {})}


def _drain(stream: Any, into: bytearray) -> None:
    for chunk in iter(lambda: stream.read(4096), b""):
        if len(into) < _EXEC_ERR_LIMIT:
            into.extend(chunk[: _EXEC_ERR_LIMIT - len(into)])


def exec_read(argv: list[str], environment: dict[str, str] | None = None) -> Iterator[bytes]:
    """Run a fixed argv and stream its stdout. Raises ExecFailed (after the stream is drained)
    if the program fails."""
    p = subprocess.Popen(  # noqa: S603  # nosec B603
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_env(environment)
    )
    assert p.stdout is not None and p.stderr is not None
    err = bytearray()
    t = threading.Thread(target=_drain, args=(p.stderr, err), daemon=True)
    t.start()
    try:
        yield from iter(lambda: p.stdout.read(65536), b"")  # type: ignore[union-attr]
    finally:
        if p.poll() is None:
            p.stdout.close()
        code = p.wait()
        t.join(5)
    if code != 0:
        raise ExecFailed(_program(argv), code, _safe_detail(bytes(err)))


def exec_write(
    argv: list[str],
    chunks: Iterable[bytes],
    timeout: float = 3600.0,
    environment: dict[str, str] | None = None,
) -> None:
    """Run a fixed argv, streaming `chunks` to its stdin. If producing chunks raises, stdin is
    closed early (the program sees a truncated input and fails) and the exception propagates."""
    p = subprocess.Popen(  # noqa: S603  # nosec B603
        argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=_env(environment)
    )
    assert p.stdin is not None and p.stderr is not None
    err = bytearray()
    t = threading.Thread(target=_drain, args=(p.stderr, err), daemon=True)
    t.start()
    failure: BaseException | None = None
    try:
        for ch in chunks:
            try:
                p.stdin.write(ch)
            except (BrokenPipeError, ConnectionResetError):
                break  # the program exited early; its exit code tells us why
    except BaseException as e:  # producer failed (e.g. backup integrity error)
        failure = e
    finally:
        with contextlib.suppress(OSError):
            p.stdin.close()
    try:
        code = p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        code = p.wait()
    t.join(5)
    if failure is not None:
        raise failure
    if code != 0:
        raise ExecFailed(_program(argv), code, _safe_detail(bytes(err)))


# --- housekeeping ---------------------------------------------------------------------------------


def cleanup_stale_verify_instances(max_age_seconds: int = 3600) -> int:
    """Remove scratch verification instances left behind by a crash."""
    n = 0
    now = dt.datetime.now(dt.UTC)
    for m in _all_meta():
        if m.purpose != "verify":
            continue
        try:
            age = (now - dt.datetime.fromisoformat(m.created)).total_seconds()
        except ValueError:
            age = max_age_seconds + 1
        if age > max_age_seconds:
            destroy_instance(m.instance_id)
            n += 1
    return n


def cleanup_orphan_volumes(min_age_seconds: int = 3600) -> list[str]:
    """Delete volumes whose instance no longer exists (e.g. a failed delete)."""
    removed: list[str] = []
    live = {m.instance_id for m in _all_meta()}
    agent_units = {st["instance_id"] for st in host_agent.list_instances()}
    for v in host_agent.list_volumes():
        name = v["instance_id"]
        base = name.removesuffix("-wal").removesuffix("-sys")
        if base in live or base in agent_units:
            continue  # an instance may be being created right now
        mtime = v.get("image_mtime")
        if mtime is not None and time.time() - float(mtime) < min_age_seconds:
            continue
        host_agent.delete_volume(name)
        removed.append(name)
    return removed

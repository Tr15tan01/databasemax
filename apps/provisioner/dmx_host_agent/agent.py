"""DatabaseMax host agent — the only root process on a data-plane host.

Runs as root under systemd (dmx-host-agent.service), standard library only, and must stay
compatible with the distribution's python3 (3.12 on Ubuntu 24.04). The unprivileged provisioner
asks it to do the few things that need root; nothing else on the host has these rights.

Interface: HTTP/1.1 over a unix socket (mode 0660, root + the provisioner's group). A closed set
of operations, every input validated, fixed argv, never a shell:

  GET    /v1/health
  GET    /v1/host                               data-plane filesystem usage, storage mode
  Volumes — one per instance and role: "<iid>" (tablespace), "<iid>-wal", "<iid>-sys" (PGDATA).
  "loop" mode: fixed-size ext4 images, loop-mounted (hard storage limits).
  "dir"  mode: plain directories on the data-plane filesystem (limits enforced softly).
  GET    /v1/volumes
  PUT    /v1/volumes/<name>    {size_mb, uid, gid}
  GET    /v1/volumes/<name>
  POST   /v1/volumes/<name>/grow {size_mb, offline}
  DELETE /v1/volumes/<name>
  Instances — each customer database cluster is a systemd unit dmx-pg@<iid>.service running the
  distribution's PostgreSQL binaries as its own system user, in its own cgroup and sandbox.
  GET    /v1/instances
  GET    /v1/instances/<iid>
  PUT    /v1/instances/<iid>   {version, port, listen, client_cidrs, memory_mb, cpus, tasks,
                                settings, admin_scram, volumes}     create (idempotent) + start
  POST   /v1/instances/<iid>/start {admin_scram}
  POST   /v1/instances/<iid>/stop
  DELETE /v1/instances/<iid>

At startup every volume is fsck'd and re-mounted before the agent reports ready to systemd; the
instance units are ordered after the agent, so their filesystems exist when they start.
"""

from __future__ import annotations

import hashlib
import http.server
import ipaddress
import json
import logging
import os
import pwd
import re
import shutil
import socket
import socketserver
import stat
import subprocess  # nosec B404 - fixed argv only, never a shell
import sys
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any

IID = re.compile(r"^[a-z0-9]{12,32}$")
VOLUME = re.compile(r"^[a-z0-9]{12,32}(-wal|-sys)?$")
VERSION = re.compile(r"^[0-9]{2}$")
SCRAM = re.compile(r"^SCRAM-SHA-256\$\d{4,6}:[A-Za-z0-9+/=]{16,64}\$[A-Za-z0-9+/=]{40,64}:[A-Za-z0-9+/=]{40,64}$")
MIN_MB = 64
MAX_MB = 1024 * 1024
UNIT_DIR = Path("/etc/systemd/system")
RUN_DIR = "/run/dmx-pg"
log = logging.getLogger("dmx.host-agent")


class AgentError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _run(argv: list[str], timeout: float = 300, stdin: str | None = None) -> str:
    """Run a fixed argv, no shell."""
    r = subprocess.run(  # noqa: S603  # nosec B603
        argv, capture_output=True, text=True, timeout=timeout, check=False, input=stdin
    )
    if r.returncode != 0:
        detail = (r.stderr or r.stdout).strip().splitlines()[-1:] or [""]
        raise AgentError(500, "command_failed", f"{Path(argv[0]).name} failed: {detail[0][:200]}")
    return r.stdout


def _int(v: Any, name: str, lo: int, hi: int) -> int:
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise AgentError(422, "invalid_request", f"{name} must be an integer between {lo} and {hi}")
    return v


class Storage:
    def __init__(self, root: str, mode: str = "loop"):
        if mode not in ("loop", "dir"):
            raise ValueError("storage mode must be loop or dir")
        self.mode = mode
        self.root = Path(root)
        self.images = self.root / "images"
        self.mounts = self.root / "mnt"
        for d in (self.root, self.images, self.mounts):
            d.mkdir(parents=True, exist_ok=True)
            # mnt/ is traversable so each instance user can reach (only) its own 0700 directory.
            os.chmod(d, 0o711 if d in (self.root, self.mounts) else 0o700)
        self._locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._guard = threading.Lock()

    # --- helpers --------------------------------------------------------------------------------
    def lock(self, name: str) -> threading.Lock:
        with self._guard:
            return self._locks[name]

    @staticmethod
    def check_name(name: str) -> str:
        if not VOLUME.match(name or ""):
            raise AgentError(422, "invalid_request", "invalid volume name")
        return name

    @staticmethod
    def check_size(size_mb: Any) -> int:
        return _int(size_mb, "size_mb", MIN_MB, MAX_MB)

    @staticmethod
    def check_id(v: Any, name: str) -> int:
        return _int(v, name, 1, 2**31 - 1)

    def image(self, name: str) -> Path:
        return self.images / f"{name}.img"

    def marker(self, name: str) -> Path:
        """Dir mode: records the volume's nominal size (there is no image)."""
        return self.images / f"{name}.dir"

    def mountpoint(self, name: str) -> Path:
        return self.mounts / name

    def exists(self, name: str) -> bool:
        return self.image(name).exists() or self.marker(name).exists()

    def is_mounted(self, name: str) -> bool:
        mp = str(self.mountpoint(name))
        with open("/proc/self/mountinfo") as f:
            return any(line.split()[4] == mp for line in f)

    def loop_device(self, name: str) -> str | None:
        mp = str(self.mountpoint(name))
        with open("/proc/self/mountinfo") as f:
            for line in f:
                parts = line.split()
                if parts[4] == mp:
                    sep = parts.index("-")
                    return parts[sep + 2]
        return None

    def info(self, name: str) -> dict[str, Any]:
        if self.marker(name).exists():
            size_mb = int(json.loads(self.marker(name).read_text()).get("size_mb", 0))
            st = os.statvfs(self.root)
            used = _du(self.mountpoint(name))
            return {
                "instance_id": name,
                "path": str(self.mountpoint(name)),
                "image_size_mb": size_mb,
                "image_mtime": int(self.marker(name).stat().st_mtime),
                "mounted": self.mountpoint(name).is_dir(),
                "hard_limit": False,
                "fs_size_bytes": size_mb * 1024 * 1024,
                "fs_used_bytes": used,
                "fs_available_bytes": min(max(size_mb * 1024 * 1024 - used, 0), st.f_bavail * st.f_frsize),
            }
        img = self.image(name)
        if not img.exists():
            raise AgentError(404, "not_found", "volume not found")
        out: dict[str, Any] = {
            "instance_id": name,
            "path": str(self.mountpoint(name)),
            "image_size_mb": img.stat().st_size // (1024 * 1024),
            "image_mtime": int(img.stat().st_mtime),
            "mounted": self.is_mounted(name),
            "hard_limit": True,
        }
        if out["mounted"]:
            st = os.statvfs(self.mountpoint(name))
            out["fs_size_bytes"] = st.f_blocks * st.f_frsize
            out["fs_used_bytes"] = (st.f_blocks - st.f_bfree) * st.f_frsize
            out["fs_available_bytes"] = st.f_bavail * st.f_frsize
            out["image_allocated_bytes"] = img.stat().st_blocks * 512  # thin-provisioned usage on host
        return out

    def _mount(self, name: str) -> None:
        mp = self.mountpoint(name)
        mp.mkdir(mode=0o700, exist_ok=True)
        if not self.is_mounted(name):
            _run(["mount", "-t", "ext4", "-o", "loop,noatime,nodev,nosuid,noexec", str(self.image(name)), str(mp)])

    # --- operations -----------------------------------------------------------------------------
    def ensure(self, name: str, size_mb: int, uid: int, gid: int) -> dict[str, Any]:
        self.check_name(name)
        self.check_size(size_mb)
        self.check_id(uid, "uid")
        self.check_id(gid, "gid")
        with self.lock(name):
            mp = self.mountpoint(name)
            if self.image(name).exists() or (self.mode == "loop" and not self.marker(name).exists()):
                img = self.image(name)
                if not img.exists():
                    tmp = img.with_suffix(".tmp")
                    with open(tmp, "wb") as f:
                        f.truncate(size_mb * 1024 * 1024)  # sparse; the provisioner's disk budget prevents overcommit
                    os.chmod(tmp, 0o600)
                    _run(["mkfs.ext4", "-q", "-F", "-m", "0", "-L", f"dmx{name.replace('-', '')[:12]}",
                          "-E", "lazy_itable_init=1,lazy_journal_init=1", str(tmp)])  # fmt: skip
                    os.replace(tmp, img)
                self._mount(name)
            else:
                mp.mkdir(mode=0o700, exist_ok=True)
                self.marker(name).write_text(json.dumps({"size_mb": size_mb}))
            st = os.stat(mp)
            if (st.st_uid, st.st_gid) != (uid, gid) or stat.S_IMODE(st.st_mode) != 0o700:
                os.chown(mp, uid, gid)
                os.chmod(mp, 0o700)
            return self.info(name)

    def grow(self, name: str, size_mb: int, offline: bool = False) -> dict[str, Any]:
        """Grow a volume. Online (mounted, in use) when the kernel allows it; `offline=True`
        is for a stopped instance: unmount, verify nothing else holds the filesystem, fsck,
        resize the image, mount again. Dir-mode volumes just record the new size."""
        self.check_name(name)
        self.check_size(size_mb)
        with self.lock(name):
            if self.marker(name).exists():
                current = int(json.loads(self.marker(name).read_text()).get("size_mb", 0))
                if size_mb < current:
                    raise AgentError(409, "shrink_not_supported", "volumes can only grow")
                self.marker(name).write_text(json.dumps({"size_mb": size_mb}))
                return self.info(name)
            img = self.image(name)
            if not img.exists():
                raise AgentError(404, "not_found", "volume not found")
            current = img.stat().st_size // (1024 * 1024)
            if size_mb < current:
                raise AgentError(409, "shrink_not_supported", "volumes can only grow")
            if offline:
                return self._grow_offline(name, size_mb)
            if size_mb == current and self._fs_matches_image(name):
                return self.info(name)
            self._mount(name)
            dev = self.loop_device(name)
            if not dev:
                raise AgentError(500, "not_mounted", "volume is not mounted")
            with open(img, "r+b") as f:
                f.truncate(size_mb * 1024 * 1024)
            _run(["losetup", "--set-capacity", dev])
            try:
                _run(["resize2fs", dev])  # online grow (needs CAP_SYS_RESOURCE)
            except AgentError as e:
                raise AgentError(409, "online_resize_unavailable", e.message) from e
            return self.info(name)

    def _fs_matches_image(self, name: str) -> bool:
        if not self.is_mounted(name):
            return False
        st = os.statvfs(self.mountpoint(name))
        image = self.image(name).stat().st_size
        return st.f_blocks * st.f_frsize > image * 0.9

    def _grow_offline(self, name: str, size_mb: int) -> dict[str, Any]:
        img = self.image(name)
        if self.is_mounted(name):
            _run(["umount", str(self.mountpoint(name))])
        # The instance must be stopped: if any other mount namespace still holds the filesystem,
        # its loop device stays attached — never resize a filesystem that is in use.
        if _run(["losetup", "--associated", str(img)]).strip():
            self._mount(name)
            raise AgentError(409, "volume_in_use", "volume is still in use; stop the instance first")
        with open(img, "r+b") as f:
            f.truncate(max(size_mb * 1024 * 1024, img.stat().st_size))
        r = subprocess.run(["e2fsck", "-f", "-p", str(img)], capture_output=True, timeout=1800, check=False)  # noqa: S603, S607  # nosec
        if r.returncode not in (0, 1):  # 1 = errors corrected
            self._mount(name)
            raise AgentError(500, "fsck_failed", f"e2fsck exited with {r.returncode}")
        _run(["resize2fs", str(img)], timeout=1800)
        self._mount(name)
        return self.info(name)

    def delete(self, name: str) -> dict[str, Any]:
        self.check_name(name)
        with self.lock(name):
            existed = self.exists(name)
            mp = self.mountpoint(name)
            if self.is_mounted(name):
                _run(["umount", str(mp)])  # loop device auto-detaches
            if self.marker(name).exists():
                if mp.is_dir() and not mp.is_symlink():
                    shutil.rmtree(mp)
                self.marker(name).unlink()
            elif mp.exists():
                mp.rmdir()
            self.image(name).unlink(missing_ok=True)
            return {"instance_id": name, "deleted": existed}

    def list(self) -> list[dict[str, Any]]:
        out = []
        names = {p.stem for p in self.images.glob("*.img")} | {p.stem for p in self.images.glob("*.dir")}
        for name in sorted(names):
            if VOLUME.match(name):
                try:
                    out.append(self.info(name))
                except AgentError:
                    continue
        return out

    def remount_all(self) -> int:
        n = 0
        for img in sorted(self.images.glob("*.img")):
            name = img.stem
            if not VOLUME.match(name) or self.is_mounted(name):
                continue
            try:
                subprocess.run(["e2fsck", "-p", str(img)], capture_output=True, timeout=600, check=False)  # noqa: S603, S607  # nosec
                self._mount(name)
                n += 1
            except Exception as e:  # keep going; the provisioner will report the instance as broken
                log.error("remount failed for %s: %s", name, e)
        return n


def _du(path: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(dirpath, f)).st_blocks * 512
            except OSError:
                continue
    return total


# --- instances ------------------------------------------------------------------------------------

# PostgreSQL settings the provisioner may choose, as integer inputs with fixed bounds; the agent
# renders them itself. Everything else is fixed here.
SETTINGS_BOUNDS = {
    "max_connections": (5, 600),
    "shared_buffers_mb": (16, 65536),
    "work_mem_mb": (1, 2048),
    "effective_cache_size_mb": (16, 262144),
    "maintenance_work_mem_mb": (16, 4096),
    "max_wal_size_mb": (64, 16384),
}


class Instances:
    def __init__(self, storage: Storage, unit_dir: Path = UNIT_DIR):
        self.storage = storage
        self.unit_dir = unit_dir
        self._locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._guard = threading.Lock()

    def lock(self, iid: str) -> threading.Lock:
        with self._guard:
            return self._locks[iid]

    @staticmethod
    def check_iid(iid: str) -> str:
        if not IID.match(iid or ""):
            raise AgentError(422, "invalid_request", "invalid instance id")
        return iid

    @staticmethod
    def user(iid: str) -> str:
        # Fits the 32-character user name limit for any instance id; collisions would need a
        # SHA-256 prefix collision between two random instance ids.
        return "dmxdb-" + hashlib.sha256(iid.encode()).hexdigest()[:12]

    @staticmethod
    def unit(iid: str) -> str:
        return f"dmx-pg@{iid}.service"

    def dropin(self, iid: str) -> Path:
        return self.unit_dir / f"{self.unit(iid)}.d" / "instance.conf"

    def paths(self, iid: str) -> dict[str, Path]:
        m = self.storage.mounts
        return {
            "sys": m / f"{iid}-sys",
            "wal": m / f"{iid}-wal",
            "data": m / iid,
            "pgdata": m / f"{iid}-sys" / "pgdata",
            "waldir": m / f"{iid}-wal" / "pg_wal",
            "tablespace": m / iid / "data",
        }

    @staticmethod
    def bindir(version: str) -> Path:
        if not VERSION.match(version or ""):
            raise AgentError(422, "invalid_request", "invalid PostgreSQL version")
        d = Path(f"/usr/lib/postgresql/{version}/bin")
        if not (d / "postgres").is_file():
            raise AgentError(422, "unsupported_version", f"PostgreSQL {version} is not installed on this host")
        return d

    # --- system user ----------------------------------------------------------------------------
    def ensure_user(self, iid: str) -> tuple[int, int]:
        name = self.user(iid)
        try:
            pw = pwd.getpwnam(name)
        except KeyError:
            _run(["useradd", "--system", "--user-group", "--no-create-home", "--home-dir", "/nonexistent",
                  "--shell", "/usr/sbin/nologin", "--comment", f"DatabaseMax instance {iid}", name])  # fmt: skip
            pw = pwd.getpwnam(name)
        return pw.pw_uid, pw.pw_gid

    # --- systemd --------------------------------------------------------------------------------
    def systemctl(self, *args: str, timeout: float = 180) -> str:
        return _run(["systemctl", *args], timeout=timeout)

    def status(self, iid: str) -> dict[str, Any]:
        props = self.systemctl(
            "show", self.unit(iid), "--property=ActiveState,SubState,MainPID,ActiveEnterTimestamp,ControlGroup,Result",
            timeout=30,
        )  # fmt: skip
        st: dict[str, str] = {}
        for line in props.splitlines():
            k, _, v = line.partition("=")
            st[k] = v
        return {
            "instance_id": iid,
            "exists": self.dropin(iid).exists(),
            "active_state": st.get("ActiveState", "unknown"),
            "sub_state": st.get("SubState", "unknown"),
            "main_pid": int(st.get("MainPID") or 0),
            "started_at": st.get("ActiveEnterTimestamp") or None,
            "control_group": st.get("ControlGroup") or None,
            "result": st.get("Result") or None,
            "user": self.user(iid),
        }

    def all_status(self) -> list[dict[str, Any]]:
        out = []
        for d in sorted(self.unit_dir.glob("dmx-pg@*.service.d")):
            iid = d.name[len("dmx-pg@") : -len(".service.d")]
            if IID.match(iid) and (d / "instance.conf").exists():
                out.append(self.status(iid))
        return out

    def _write_dropin(self, iid: str, cfg: dict[str, Any], uid_name: str) -> None:
        p = self.paths(iid)
        s = cfg["settings"]
        # Always on loopback (the provisioner's admin connections); plus the private address on
        # worker hosts, where the gateway runs on another VM.
        listen = "127.0.0.1" if cfg["listen"] == "127.0.0.1" else f"127.0.0.1,{cfg['listen']}"
        args = {
            "listen_addresses": listen,
            "port": str(cfg["port"]),
            "max_connections": str(s["max_connections"]),
            "superuser_reserved_connections": "3",
            "shared_buffers": f"{s['shared_buffers_mb']}MB",
            "effective_cache_size": f"{s['effective_cache_size_mb']}MB",
            "work_mem": f"{s['work_mem_mb']}MB",
            "maintenance_work_mem": f"{s['maintenance_work_mem_mb']}MB",
            "max_wal_size": f"{s['max_wal_size_mb']}MB",
            "min_wal_size": "32MB",
            # Connection hygiene defaults (a session can SET its own values).
            "idle_in_transaction_session_timeout": "30min",
            "tcp_keepalives_idle": "60",
            "tcp_keepalives_interval": "15",
            "tcp_keepalives_count": "4",
            "max_worker_processes": "4",
            "max_parallel_workers": "2",
            "password_encryption": "scram-sha-256",
            "unix_socket_directories": f"{RUN_DIR}/{iid}",
            "ssl": "off",  # TLS terminates at the gateway; instances listen on a private address only
            "jit": "off",  # no W^X mappings (the unit sets MemoryDenyWriteExecute)
            "huge_pages": "off",
            "logging_collector": "off",
            "log_statement": "none",
            "log_min_duration_statement": "-1",
            "log_connections": "off",
            "log_min_messages": "warning",
            "shared_preload_libraries": "",
        }
        cmd = [str(self.bindir(cfg["version"]) / "postgres"), "-D", str(p["pgdata"])]
        for k, v in args.items():
            cmd += ["-c", f"{k}={v}"]
        allow = " ".join(["localhost", *cfg["client_cidrs"]])
        text = "\n".join(
            [
                "# Managed by the DatabaseMax host agent — do not edit.",
                "[Service]",
                f"User={uid_name}",
                f"Group={uid_name}",
                "ExecStart=",
                "ExecStart=" + " ".join(cmd),
                f"BindPaths={p['sys']} {p['wal']} {p['data']}",
                f"MemoryMax={cfg['memory_mb']}M",
                "MemorySwapMax=0",
                f"CPUQuota={max(1, round(cfg['cpus'] * 100))}%",
                f"TasksMax={cfg['tasks']}",
                f"IPAddressAllow={allow}",
                "",
            ]
        )
        d = self.dropin(iid)
        d.parent.mkdir(mode=0o755, exist_ok=True)
        tmp = d.with_suffix(".tmp")
        tmp.write_text(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, d)
        self.systemctl("daemon-reload", timeout=60)

    def _write_hba(self, iid: str, client_cidrs: list[str]) -> None:
        pgdata = self.paths(iid)["pgdata"]
        rules = "".join(f"host    all  all             {c:<18} scram-sha-256\n" for c in client_cidrs)
        text = (
            "# Managed by DatabaseMax — do not edit.\n"
            "local   all  dmx_admin                          trust\n"
            "host    all  dmx_admin       127.0.0.1/32       scram-sha-256\n"
            "host    all  dmx_admin       ::1/128            scram-sha-256\n"
            "host    all  dmx_admin       all                reject\n"
            # Short-lived, non-superuser restore roles (restores never run customer SQL as a superuser).
            "host    all  +dmx_restorers  127.0.0.1/32       scram-sha-256\n"
            "host    all  +dmx_restorers  all                reject\n"
            f"{rules}"
            "host    all  all             0.0.0.0/0          reject\n"
            "host    all  all             ::/0               reject\n"
        )
        st = os.stat(pgdata)
        f = pgdata / "pg_hba.conf"
        f.write_text(text)
        os.chown(f, st.st_uid, st.st_gid)
        os.chmod(f, 0o600)

    def _set_admin_password(self, iid: str, version: str, admin_scram: str) -> None:
        """Single-user mode (the instance must be stopped); the verifier is validated, never a
        plaintext password."""
        _run(
            ["runuser", "-u", self.user(iid), "--", str(self.bindir(version) / "postgres"), "--single", "-j",
             "-D", str(self.paths(iid)["pgdata"]), "postgres"],
            stdin=f"ALTER ROLE dmx_admin WITH PASSWORD '{admin_scram}';\n",
            timeout=120,
        )  # fmt: skip

    def validate(self, body: dict[str, Any]) -> dict[str, Any]:
        version = body.get("version")
        self.bindir(version if isinstance(version, str) else "")
        listen = body.get("listen")
        try:
            ipaddress.ip_address(listen if isinstance(listen, str) else "")
        except ValueError as e:
            raise AgentError(422, "invalid_request", "invalid listen address") from e
        cidrs = body.get("client_cidrs")
        if not isinstance(cidrs, list) or len(cidrs) > 16:
            raise AgentError(422, "invalid_request", "client_cidrs must be a list")
        clean = []
        for c in cidrs:
            try:
                clean.append(str(ipaddress.ip_network(c if isinstance(c, str) else "")))
            except ValueError as e:
                raise AgentError(422, "invalid_request", "invalid client CIDR") from e
        settings = body.get("settings")
        if not isinstance(settings, dict) or set(settings) != set(SETTINGS_BOUNDS):
            raise AgentError(422, "invalid_request", "settings must contain exactly the allowed keys")
        for k, (lo, hi) in SETTINGS_BOUNDS.items():
            _int(settings[k], k, lo, hi)
        cpus = body.get("cpus")
        if not isinstance(cpus, (int, float)) or isinstance(cpus, bool) or not 0.05 <= cpus <= 64:
            raise AgentError(422, "invalid_request", "cpus out of range")
        scram = body.get("admin_scram")
        if not isinstance(scram, str) or not SCRAM.match(scram):
            raise AgentError(422, "invalid_request", "invalid admin verifier")
        vols = body.get("volumes")
        if not isinstance(vols, dict) or set(vols) != {"sys", "wal", "data"}:
            raise AgentError(422, "invalid_request", "volumes must be {sys, wal, data}")
        return {
            "version": version,
            "port": _int(body.get("port"), "port", 1024, 65535),
            "listen": listen,
            "client_cidrs": clean,
            "memory_mb": _int(body.get("memory_mb"), "memory_mb", 64, 1024 * 1024),
            "cpus": float(cpus),
            "tasks": _int(body.get("tasks"), "tasks", 32, 32768),
            "settings": settings,
            "admin_scram": scram,
            "volumes": {k: Storage.check_size(v) for k, v in vols.items()},
        }

    def ensure(self, iid: str, body: dict[str, Any]) -> dict[str, Any]:
        self.check_iid(iid)
        cfg = self.validate(body)
        with self.lock(iid):
            if self.dropin(iid).exists() and self.status(iid)["active_state"] == "active":
                return self.status(iid)
            uid, gid = self.ensure_user(iid)
            p = self.paths(iid)
            for role, name in (("sys", f"{iid}-sys"), ("wal", f"{iid}-wal"), ("data", iid)):
                self.storage.ensure(name, cfg["volumes"][role], uid, gid)
            if not (p["pgdata"] / "PG_VERSION").exists():
                if p["pgdata"].exists():  # leftover from an interrupted initdb
                    shutil.rmtree(p["pgdata"])
                if p["waldir"].exists():
                    shutil.rmtree(p["waldir"])
                _run(
                    ["runuser", "-u", self.user(iid), "--", str(self.bindir(cfg["version"]) / "initdb"),
                     "-D", str(p["pgdata"]), "-U", "dmx_admin", "--auth-local=trust", "--auth-host=reject",
                     "-E", "UTF8", "--locale=C.UTF-8", "--data-checksums", f"--waldir={p['waldir']}"],
                    timeout=300,
                )  # fmt: skip
            if not p["tablespace"].exists():
                p["tablespace"].mkdir(mode=0o700)
                os.chown(p["tablespace"], uid, gid)
            self._write_hba(iid, cfg["client_cidrs"])
            self._write_dropin(iid, cfg, self.user(iid))
            self._set_admin_password(iid, cfg["version"], cfg["admin_scram"])
            self.systemctl("enable", "--now", self.unit(iid))
            return self.status(iid)

    def _version(self, iid: str) -> str:
        v = (self.paths(iid)["pgdata"] / "PG_VERSION").read_text().strip()
        self.bindir(v)
        return v

    def start(self, iid: str, admin_scram: Any) -> dict[str, Any]:
        self.check_iid(iid)
        if not isinstance(admin_scram, str) or not SCRAM.match(admin_scram):
            raise AgentError(422, "invalid_request", "invalid admin verifier")
        with self.lock(iid):
            if not self.dropin(iid).exists():
                raise AgentError(404, "not_found", "instance not found")
            if self.status(iid)["active_state"] != "active":
                # Like a container entrypoint: the admin verifier is (re)applied on every start,
                # so rotating PROVISIONER_ADMIN_SECRET takes effect at the next restart.
                self._set_admin_password(iid, self._version(iid), admin_scram)
                self.systemctl("enable", "--now", self.unit(iid))
            return self.status(iid)

    def stop(self, iid: str) -> dict[str, Any]:
        self.check_iid(iid)
        with self.lock(iid):
            if self.dropin(iid).exists():
                self.systemctl("disable", "--now", self.unit(iid))
            return self.status(iid)

    def destroy(self, iid: str) -> dict[str, Any]:
        """Idempotent: stop the unit, remove its configuration, filesystems and system user."""
        self.check_iid(iid)
        with self.lock(iid):
            removed = False
            d = self.dropin(iid)
            if d.exists():
                self.systemctl("disable", "--now", self.unit(iid))
                d.unlink()
                if not any(d.parent.iterdir()):
                    d.parent.rmdir()
                self.systemctl("daemon-reload", timeout=60)
                removed = True
            for name in (iid, f"{iid}-wal", f"{iid}-sys"):
                removed = bool(self.storage.delete(name).get("deleted")) or removed
            try:
                pwd.getpwnam(self.user(iid))
                _run(["userdel", self.user(iid)])
                removed = True
            except KeyError:
                pass
            return {"instance_id": iid, "removed": removed}


class Handler(http.server.BaseHTTPRequestHandler):
    server: AgentServer
    protocol_version = "HTTP/1.1"

    def address_string(self) -> str:  # unix sockets have no peer address
        return "unix"

    def log_message(self, fmt: str, *args: Any) -> None:
        log.info("%s %s", self.command, self.path)

    def _send(self, status: int, body: Any) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict[str, Any]:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError as e:
            raise AgentError(400, "bad_request", "invalid Content-Length") from e
        if n < 0 or n > 8192:
            raise AgentError(413, "too_large", "request too large")
        if not n:
            return {}
        try:
            body = json.loads(self.rfile.read(n))
        except ValueError as e:
            raise AgentError(400, "invalid_json", "invalid JSON") from e
        if not isinstance(body, dict):
            raise AgentError(400, "invalid_json", "expected an object")
        return body

    def _dispatch(self) -> None:
        s = self.server.storage
        inst = self.server.instances
        parts = [p for p in self.path.split("?")[0].split("/") if p]
        cmd = self.command
        try:
            if parts == ["v1", "health"] and cmd == "GET":
                return self._send(200, {"status": "ok"})
            if parts == ["v1", "host"] and cmd == "GET":
                st = os.statvfs(s.root)
                return self._send(200, {
                    "storage_mode": s.mode,
                    "fs_size_bytes": st.f_blocks * st.f_frsize,
                    "fs_used_bytes": (st.f_blocks - st.f_bfree) * st.f_frsize,
                    "fs_available_bytes": st.f_bavail * st.f_frsize,
                    "volumes": len(s.list()),
                })  # fmt: skip
            if parts == ["v1", "volumes"] and cmd == "GET":
                return self._send(200, s.list())
            if len(parts) == 3 and parts[:2] == ["v1", "volumes"]:
                name = s.check_name(parts[2])
                if cmd == "PUT":
                    b = self._body()
                    return self._send(
                        200, s.ensure(name, s.check_size(b.get("size_mb")), s.check_id(b.get("uid"), "uid"),
                                      s.check_id(b.get("gid"), "gid"))
                    )  # fmt: skip
                if cmd == "GET":
                    return self._send(200, s.info(name))
                if cmd == "DELETE":
                    return self._send(200, s.delete(name))
            if len(parts) == 4 and parts[:2] == ["v1", "volumes"] and parts[3] == "grow" and cmd == "POST":
                b = self._body()
                return self._send(200, s.grow(s.check_name(parts[2]), s.check_size(b.get("size_mb")),
                                              offline=b.get("offline") is True))  # fmt: skip
            if parts == ["v1", "instances"] and cmd == "GET":
                return self._send(200, inst.all_status())
            if len(parts) == 3 and parts[:2] == ["v1", "instances"]:
                iid = inst.check_iid(parts[2])
                if cmd == "GET":
                    found = inst.status(iid)
                    if not found["exists"]:
                        raise AgentError(404, "not_found", "instance not found")
                    return self._send(200, found)
                if cmd == "PUT":
                    return self._send(200, inst.ensure(iid, self._body()))
                if cmd == "DELETE":
                    return self._send(200, inst.destroy(iid))
            if len(parts) == 4 and parts[:2] == ["v1", "instances"] and cmd == "POST":
                iid = inst.check_iid(parts[2])
                if parts[3] == "start":
                    return self._send(200, inst.start(iid, self._body().get("admin_scram")))
                if parts[3] == "stop":
                    return self._send(200, inst.stop(iid))
            raise AgentError(404, "not_found", "no such operation")
        except AgentError as e:
            self._send(e.status, {"code": e.code, "message": e.message})
        except subprocess.TimeoutExpired:
            log.error("command timed out")
            self._send(504, {"code": "timeout", "message": "operation timed out"})
        except Exception as e:  # never leak internals
            log.exception("operation failed")
            self._send(500, {"code": "agent_error", "message": type(e).__name__})

    do_GET = do_PUT = do_POST = do_DELETE = _dispatch


class AgentServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, path: str, storage: Storage, gid: int | None, instances: Instances | None = None):
        if os.path.exists(path):
            os.unlink(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        old = os.umask(0o117)
        try:
            super().__init__(path, Handler)
        finally:
            os.umask(old)
        if gid is not None:
            os.chown(path, 0, gid)
        os.chmod(path, 0o660)  # nosec B103 - unix socket: root + the provisioner group only
        self.storage = storage
        self.instances = instances or Instances(storage)


def _sd_notify(msg: str) -> None:
    """Tell systemd we're ready (Type=notify) without depending on libsystemd."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
        s.connect(addr)
        s.sendall(msg.encode())


def main() -> None:
    logging.basicConfig(stream=sys.stdout, level=logging.INFO,
                        format='{"level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')  # fmt: skip
    root = os.environ.get("DMX_STORAGE_ROOT", "/var/lib/databasemax/data-plane")
    sock = os.environ.get("DMX_AGENT_SOCKET", "/run/databasemax/agent.sock")
    gid_env = os.environ.get("DMX_AGENT_SOCKET_GID")
    group = os.environ.get("DMX_AGENT_SOCKET_GROUP")
    if group and not gid_env:
        import grp

        gid_env = str(grp.getgrnam(group).gr_gid)
    mode = os.environ.get("DMX_STORAGE_BACKEND", "loop")
    if os.geteuid() != 0:
        sys.exit("the host agent must run as root (it manages filesystems, system users and units)")
    storage = Storage(root, mode=mode)
    n = storage.remount_all()
    log.info("storage mode %s, remounted %d volume(s)", mode, n)
    server = AgentServer(sock, storage, int(gid_env) if gid_env else None)
    log.info("listening on %s", sock)
    _sd_notify("READY=1")
    server.serve_forever()


if __name__ == "__main__":
    main()

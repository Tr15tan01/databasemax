"""Client for the host agent (root, unix socket): per-instance filesystems and the systemd units
that run customer PostgreSQL servers. See dmx_host_agent/agent.py for the operations."""

from __future__ import annotations

import http.client
import json
import socket
from typing import Any

from dmx_provisioner.config import get_settings


class HostAgentError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.status = status
        self.code = code
        self.message = message


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self._path)
        self.sock = s


def _call(method: str, path: str, body: dict[str, Any] | None = None, timeout: float = 600) -> Any:
    conn = _UnixConnection(get_settings().agent_socket, timeout)
    try:
        payload = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if payload else {}
        conn.request(method, path, body=payload, headers=headers)
        r = conn.getresponse()
        data = json.loads(r.read() or b"null")
    except OSError as e:
        raise HostAgentError(503, "agent_unreachable", type(e).__name__) from e
    finally:
        conn.close()
    if r.status >= 400:
        raise HostAgentError(r.status, data.get("code", "agent_error"), data.get("message", ""))
    return data


def health() -> dict[str, Any]:
    return _call("GET", "/v1/health", timeout=5)


def host() -> dict[str, Any]:
    return _call("GET", "/v1/host", timeout=30)


# --- volumes --------------------------------------------------------------------------------------


def grow(name: str, *, size_mb: int, offline: bool = False) -> dict[str, Any]:
    return _call("POST", f"/v1/volumes/{name}/grow", {"size_mb": size_mb, "offline": offline}, timeout=3600)


def get_volume(name: str) -> dict[str, Any] | None:
    try:
        return _call("GET", f"/v1/volumes/{name}", timeout=30)
    except HostAgentError as e:
        if e.status == 404:
            return None
        raise


def delete_volume(name: str) -> dict[str, Any]:
    return _call("DELETE", f"/v1/volumes/{name}")


def list_volumes() -> list[dict[str, Any]]:
    return _call("GET", "/v1/volumes", timeout=30)


# --- instances ------------------------------------------------------------------------------------


def ensure_instance(iid: str, spec: dict[str, Any]) -> dict[str, Any]:
    return _call("PUT", f"/v1/instances/{iid}", spec, timeout=600)


def instance(iid: str) -> dict[str, Any] | None:
    try:
        return _call("GET", f"/v1/instances/{iid}", timeout=30)
    except HostAgentError as e:
        if e.status == 404:
            return None
        raise


def list_instances() -> list[dict[str, Any]]:
    return _call("GET", "/v1/instances", timeout=60)


def start(iid: str, *, admin_scram: str) -> dict[str, Any]:
    return _call("POST", f"/v1/instances/{iid}/start", {"admin_scram": admin_scram}, timeout=300)


def stop(iid: str) -> dict[str, Any]:
    return _call("POST", f"/v1/instances/{iid}/stop", timeout=300)


def destroy(iid: str) -> dict[str, Any]:
    return _call("DELETE", f"/v1/instances/{iid}", timeout=600)

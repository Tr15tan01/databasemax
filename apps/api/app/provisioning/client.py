"""Client for the provisioner service (private network, bearer token)."""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import get_settings


class ProvisionerError(Exception):
    def __init__(self, code: str, message: str, status: int | None = None, retryable: bool = True):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable


class ProvisionerClient:
    def __init__(self, base_url: str | None = None, token: str | None = None, timeout: float = 120.0):
        s = get_settings()
        self.base_url = (base_url or s.provisioner_url).rstrip("/")
        self._token = token or s.provisioner_token.get_secret_value()
        self.timeout = timeout

    def _req(self, method: str, path: str, **kw: Any) -> Any:
        try:
            kw.setdefault("timeout", self.timeout)
            r = httpx.request(
                method,
                f"{self.base_url}{path}",
                headers={"Authorization": f"Bearer {self._token}"},
                **kw,
            )
        except httpx.HTTPError as e:
            raise ProvisionerError("provisioner_unreachable", type(e).__name__) from e
        if r.status_code >= 400:
            try:
                body = r.json()
            except ValueError:
                body = {}
            code = body.get("code") or body.get("detail") or f"http_{r.status_code}"
            retryable = r.status_code >= 500 or r.status_code in (409, 429)
            if code == "capacity_exceeded":
                retryable = True
            raise ProvisionerError(str(code), str(body.get("message", "")), r.status_code, retryable)
        return r.json()

    def health(self) -> dict[str, Any]:
        return self._req("GET", "/v1/health")

    def capacity(self) -> dict[str, Any]:
        return self._req("GET", "/v1/capacity")

    def list_instances(self) -> list[dict[str, Any]]:
        return self._req("GET", "/v1/instances")

    def get_instance(self, iid: str) -> dict[str, Any] | None:
        try:
            return self._req("GET", f"/v1/instances/{iid}")
        except ProvisionerError as e:
            if e.status == 404:
                return None
            raise

    def ensure_instance(self, iid: str, **body: Any) -> dict[str, Any]:
        return self._req("PUT", f"/v1/instances/{iid}", json=body)

    def configure(self, iid: str, **body: Any) -> dict[str, Any]:
        return self._req("POST", f"/v1/instances/{iid}/configure", json=body)

    def rotate(self, iid: str, **body: Any) -> dict[str, Any]:
        return self._req("POST", f"/v1/instances/{iid}/rotate", json=body)

    def set_connection_limit(self, iid: str, **body: Any) -> dict[str, Any]:
        return self._req("POST", f"/v1/instances/{iid}/connection-limit", json=body)

    def set_storage(self, iid: str, *, storage_mb: int) -> dict[str, Any]:
        return self._req("POST", f"/v1/instances/{iid}/storage", json={"storage_mb": storage_mb}, timeout=3600.0)

    def set_read_only(self, iid: str, **body: Any) -> dict[str, Any]:
        return self._req("POST", f"/v1/instances/{iid}/read-only", json=body)

    def stop(self, iid: str) -> dict[str, Any]:
        return self._req("POST", f"/v1/instances/{iid}/stop")

    def start(self, iid: str) -> dict[str, Any]:
        return self._req("POST", f"/v1/instances/{iid}/start")

    def destroy(self, iid: str) -> dict[str, Any]:
        return self._req("DELETE", f"/v1/instances/{iid}")

    def stats(self, iid: str, database: str, username: str) -> dict[str, Any]:
        return self._req(
            "GET", f"/v1/instances/{iid}/stats", params={"database": database, "username": username}, timeout=30.0
        )

    def live(self, iid: str, database: str, username: str) -> dict[str, Any]:
        return self._req(
            "GET", f"/v1/instances/{iid}/live", params={"database": database, "username": username}, timeout=10.0
        )

    # --- backups (long-running: generous timeouts) ------------------------------------------
    def create_backup(self, iid: str, *, database: str, database_id: str, backup_id: str) -> dict[str, Any]:
        return self._req(
            "POST",
            f"/v1/instances/{iid}/backups",
            json={"database": database, "database_id": database_id, "backup_id": backup_id},
            timeout=3600.0,
        )

    def restore(self, iid: str, **body: Any) -> dict[str, Any]:
        return self._req("POST", f"/v1/instances/{iid}/restore", json=body, timeout=3600.0)

    def verify_backup(self, **body: Any) -> dict[str, Any]:
        return self._req("POST", "/v1/backups/verify", json=body, timeout=3600.0)

    def cleanup_orphans(self) -> dict[str, Any]:
        return self._req("POST", "/v1/maintenance/orphan-volumes", timeout=300.0)

    def terminate_sessions(self, iid: str, *, username: str) -> dict[str, Any]:
        return self._req("POST", f"/v1/instances/{iid}/terminate-sessions", json={"username": username})

    def relay_backup_to(
        self, dst: ProvisionerClient, *, database_id: str, backup_id: str, sha256: str
    ) -> dict[str, Any]:
        """Copy an encrypted backup object from this host's store to `dst`'s store. Only
        ciphertext passes through the control plane; the receiver checks the SHA-256."""
        url = f"{self.base_url}/v1/backups/{database_id}/{backup_id}/object"
        try:
            with httpx.stream(
                "GET", url, headers={"Authorization": f"Bearer {self._token}"}, timeout=httpx.Timeout(3600.0)
            ) as r:
                if r.status_code >= 400:
                    raise ProvisionerError("backup_missing", "source object unavailable", r.status_code, False)
                return dst._req(
                    "PUT",
                    f"/v1/backups/{database_id}/{backup_id}/object",
                    params={"sha256": sha256},
                    content=r.iter_bytes(1024 * 1024),
                    timeout=3600.0,
                )
        except httpx.HTTPError as e:
            raise ProvisionerError("provisioner_unreachable", type(e).__name__) from e

    def delete_backup(self, database_id: str, backup_id: str) -> dict[str, Any]:
        return self._req("DELETE", f"/v1/backups/{database_id}/{backup_id}", timeout=120.0)


def get_provisioner() -> ProvisionerClient:
    return ProvisionerClient()

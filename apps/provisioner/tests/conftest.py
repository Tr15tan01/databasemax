"""Shared fixtures for the data-plane integration tests.

Those tests create real system users, filesystems and `dmx-pg@` systemd units, so they only run
on a host prepared with infra/scripts/host-setup.sh, as root, and only when asked to
(DMX_HOST_TESTS=1). They start their own host agent in-process with a separate data-plane root,
socket and provisioner state directory, so they never touch the host's real instances.
"""

from __future__ import annotations

import os
import secrets
import shutil
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

HOST_TESTS = os.environ.get("DMX_HOST_TESTS") == "1"
needs_host = pytest.mark.skipif(
    not HOST_TESTS or not hasattr(os, "geteuid") or os.geteuid() != 0,
    reason="data-plane tests need root on a host prepared by host-setup.sh (set DMX_HOST_TESTS=1)",
)


@pytest.fixture(scope="module")
def host(tmp_path_factory) -> Iterator[dict[str, str]]:  # type: ignore[no-untyped-def]
    """An in-process host agent plus provisioner settings pointing at it. Storage mode follows
    DMX_TEST_STORAGE_BACKEND (default "dir"; "loop" for hard-limit tests)."""
    from dmx_host_agent.agent import AgentServer, Storage
    from dmx_provisioner import backup_store, config

    mode = os.environ.get("DMX_TEST_STORAGE_BACKEND", "dir")
    # Under /var/lib/databasemax: the instance units' sandbox maps that tree (not /tmp).
    root = Path("/var/lib/databasemax") / f"test-{secrets.token_hex(4)}"
    sock_dir = tmp_path_factory.mktemp("agent")
    sock = str(sock_dir / "agent.sock")
    server = AgentServer(sock, Storage(str(root / "data-plane"), mode=mode), gid=None)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    mp = pytest.MonkeyPatch()
    for k, v in {
        "DMX_AGENT_SOCKET": sock,
        "DMX_STORAGE_BACKEND": mode,
        "DMX_STORAGE_ROOT": str(root / "data-plane"),
        "DMX_PROVISIONER_STATE_DIR": str(root / "provisioner"),
        "DMX_BACKUP_DIR": str(tmp_path_factory.mktemp("backups")),
        "DMX_BACKUP_STORE": "local",
        "DMX_INSTANCE_PORT_MIN": "29000",
        "DMX_INSTANCE_PORT_MAX": "29999",
    }.items():
        mp.setenv(k, v)
    config.get_settings.cache_clear()
    backup_store.reset_store()
    yield {"root": str(root), "storage_root": str(root / "data-plane"), "mode": mode}
    mp.undo()
    config.get_settings.cache_clear()
    backup_store.reset_store()
    server.shutdown()
    server.server_close()
    shutil.rmtree(root, ignore_errors=True)

"""Host agent storage (loop-mounted ext4 per volume). Needs root and loop devices — the agent's
production environment. Skipped, with the reason shown, when not running as root."""

import os
import threading
from pathlib import Path

import pytest

pytest.importorskip("pwd", reason="the host agent runs on Linux")
from dmx_host_agent.agent import AgentError, AgentServer, Storage

pytestmark = pytest.mark.skipif(os.geteuid() != 0, reason="host agent storage tests need root + loop devices")


@pytest.fixture
def storage(tmp_path):  # type: ignore[no-untyped-def]
    s = Storage(str(tmp_path / "dp"))
    yield s
    for v in s.list():
        s.delete(v["instance_id"])


def test_create_is_hard_limited_owned_and_idempotent(storage) -> None:  # type: ignore[no-untyped-def]
    v = storage.ensure("abcdefabcdef12", 80, 102, 104)
    assert v["mounted"] and 60 * 2**20 < v["fs_size_bytes"] <= 80 * 2**20
    mp = Path(v["path"])
    st = mp.stat()
    assert (st.st_uid, st.st_gid, st.st_mode & 0o777) == (102, 104, 0o700)
    assert v["image_allocated_bytes"] < 20 * 2**20  # sparse on the host
    again = storage.ensure("abcdefabcdef12", 80, 102, 104)
    assert again["path"] == v["path"]
    # Filling the filesystem stops at its size (hard limit), not at the host's free space.
    with pytest.raises(OSError) as ei, open(mp / "fill", "wb") as f:
        for _ in range(200):
            f.write(b"\0" * 2**20)
            f.flush()
            os.fsync(f.fileno())
    assert ei.value.errno == 28  # ENOSPC
    (mp / "fill").unlink()


def _can_resize_online() -> bool:
    cap = int(Path("/proc/self/status").read_text().split("CapEff:")[1].split()[0], 16)
    return bool(cap >> 24 & 1)  # CAP_SYS_RESOURCE


def test_grow_and_refuse_shrink(storage) -> None:  # type: ignore[no-untyped-def]
    storage.ensure("growgrowgrow01", 64, 102, 104)
    (Path(storage.mountpoint("growgrowgrow01")) / "keep").write_text("data")
    if _can_resize_online():
        big = storage.grow("growgrowgrow01", 160)
    else:
        # This host lacks CAP_SYS_RESOURCE (e.g. a sandbox): online resize is refused explicitly…
        with pytest.raises(AgentError) as ei:
            storage.grow("growgrowgrow01", 160)
        assert ei.value.code == "online_resize_unavailable"
        # …and the offline path works once nothing else holds the filesystem.
        big = storage.grow("growgrowgrow01", 160, offline=True)
    assert big["fs_size_bytes"] > 140 * 2**20 and big["mounted"]
    assert (Path(big["path"]) / "keep").read_text() == "data"
    with pytest.raises(AgentError) as ei:
        storage.grow("growgrowgrow01", 64)
    assert ei.value.code == "shrink_not_supported"


def test_remount_after_unmount(storage) -> None:  # type: ignore[no-untyped-def]
    storage.ensure("remountremount", 64, 102, 104)
    (Path(storage.mountpoint("remountremount")) / "f").write_text("x")
    os.system(f"umount {storage.mountpoint('remountremount')}")
    assert not storage.is_mounted("remountremount")
    assert storage.remount_all() == 1
    assert (Path(storage.mountpoint("remountremount")) / "f").read_text() == "x"


def test_delete_and_validation(storage) -> None:  # type: ignore[no-untyped-def]
    storage.ensure("deletedelete01-wal", 64, 102, 104)
    assert storage.delete("deletedelete01-wal")["deleted"] is True
    assert storage.delete("deletedelete01-wal")["deleted"] is False
    assert not storage.image("deletedelete01-wal").exists()
    for bad in ("../../etc", "ABC", "x", "abcdefabcdef12;rm", "abcdefabcdef12-log"):
        with pytest.raises(AgentError):
            storage.ensure(bad, 64, 102, 104)
    for size in (1, 63, 10**9, "64", True):
        with pytest.raises(AgentError):
            storage.ensure("abcdefabcdef99", size, 102, 104)  # type: ignore[arg-type]
    with pytest.raises(AgentError):
        storage.ensure("abcdefabcdef99", 64, 0, 104)  # never root-owned


def test_http_interface_over_unix_socket(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    sock = str(tmp_path / "agent.sock")
    server = AgentServer(sock, Storage(str(tmp_path / "dp2")), gid=None)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        assert oct(os.stat(sock).st_mode & 0o777) == "0o660"
        monkeypatch.setenv("DMX_AGENT_SOCKET", sock)
        from dmx_provisioner import config, host_agent

        config.get_settings.cache_clear()
        assert host_agent.health() == {"status": "ok"}
        assert host_agent.host()["storage_mode"] == "loop"
        server.storage.ensure("httphttphttp01", 64, 102, 104)
        assert host_agent.get_volume("httphttphttp01")["mounted"]
        assert [x["instance_id"] for x in host_agent.list_volumes()] == ["httphttphttp01"]
        with pytest.raises(host_agent.HostAgentError) as ei:
            host_agent.get_volume("BAD")
        assert ei.value.status == 422
        assert host_agent.delete_volume("httphttphttp01")["deleted"] is True
        assert host_agent.get_volume("httphttphttp01") is None
    finally:
        server.shutdown()
        server.server_close()
        config.get_settings.cache_clear()

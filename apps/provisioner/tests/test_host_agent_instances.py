"""Host agent instance management: input validation and the files it renders (systemd drop-in,
pg_hba.conf). Runs without root: systemctl is stubbed and paths point into a temp directory."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pwd", reason="the host agent runs on Linux")
from dmx_host_agent import agent
from dmx_host_agent.agent import AgentError, Instances, Storage

SCRAM = "SCRAM-SHA-256$4096:c2FsdHNhbHRzYWx0c2FsdA==$" + "A" * 43 + "=:" + "B" * 43 + "="


def spec(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "version": "16",
        "port": 20001,
        "listen": "127.0.0.1",
        "client_cidrs": ["127.0.0.1/32"],
        "memory_mb": 256,
        "cpus": 0.25,
        "tasks": 256,
        "settings": {
            "max_connections": 25,
            "shared_buffers_mb": 48,
            "work_mem_mb": 2,
            "effective_cache_size_mb": 153,
            "maintenance_work_mem_mb": 16,
            "max_wal_size_mb": 64,
        },
        "admin_scram": SCRAM,
        "volumes": {"sys": 384, "wal": 320, "data": 1100},
    }
    base.update(over)
    return base


@pytest.fixture
def inst(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Instances:
    monkeypatch.setattr(Instances, "bindir", staticmethod(lambda v: Path(f"/usr/lib/postgresql/{v}/bin")))
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(Instances, "systemctl", lambda self, *a, **k: calls.append(a) or "")
    i = Instances(Storage(str(tmp_path / "dp"), mode="dir"), unit_dir=tmp_path / "units")
    i.calls = calls  # type: ignore[attr-defined]
    return i


def test_validation_rejects_anything_outside_the_closed_set(inst: Instances) -> None:
    assert inst.validate(spec())["port"] == 20001
    bad = [
        {"version": "16; rm -rf /"},
        {"port": 80},
        {"listen": "0.0.0.0 -c ssl=on"},
        {"client_cidrs": ["10.0.0.0/8 trust"]},
        {"settings": {"max_connections": 25}},  # missing keys
        {"settings": {**spec()["settings"], "shared_preload_libraries": 1}},  # extra key
        {"settings": {**spec()["settings"], "work_mem_mb": "2MB"}},
        {"cpus": 0},
        {"admin_scram": "md5abc"},
        {"admin_scram": SCRAM + "';DROP ROLE x;--"},
        {"volumes": {"sys": 384, "wal": 320}},
        {"memory_mb": True},
    ]
    for override in bad:
        with pytest.raises(AgentError) as ei:
            inst.validate(spec(**override))
        assert ei.value.status == 422, override
    for iid in ("../etc", "ABCDEFABCDEF12", "short", "abcdefabcdef12;x"):
        with pytest.raises(AgentError):
            inst.check_iid(iid)


def test_user_names_are_unique_and_valid() -> None:
    a, b = Instances.user("abcdefabcdef12"), Instances.user("abcdefabcdef13")
    assert a != b and a.startswith("dmxdb-") and len(a) <= 32
    assert Instances.user("x" * 32) == Instances.user("x" * 32)


def test_dropin_limits_sandbox_inputs_and_network(inst: Instances) -> None:
    cfg = inst.validate(spec(listen="10.0.1.12", client_cidrs=["10.0.1.11/32"]))
    inst._write_dropin("abcdefabcdef12", cfg, "dmxdb-0123456789ab")
    text = inst.dropin("abcdefabcdef12").read_text()
    assert "User=dmxdb-0123456789ab" in text
    assert "MemoryMax=256M" in text and "MemorySwapMax=0" in text and "CPUQuota=25%" in text and "TasksMax=256" in text
    assert "IPAddressAllow=localhost 10.0.1.11/32" in text
    exec_line = next(line for line in text.splitlines() if line.startswith("ExecStart=/"))
    assert exec_line.startswith("ExecStart=/usr/lib/postgresql/16/bin/postgres -D ")
    assert "-c listen_addresses=127.0.0.1,10.0.1.12" in exec_line and "-c port=20001" in exec_line
    assert "-c ssl=off" in exec_line and "-c jit=off" in exec_line and "-c max_connections=25" in exec_line
    assert "%" not in exec_line and "'" not in exec_line  # nothing systemd or a shell would reinterpret
    p = inst.paths("abcdefabcdef12")
    assert f"BindPaths={p['sys']} {p['wal']} {p['data']}" in text
    assert ("daemon-reload",) in inst.calls  # type: ignore[attr-defined]


def test_pg_hba_allows_admin_only_on_loopback(inst: Instances) -> None:
    pgdata = inst.paths("abcdefabcdef12")["pgdata"]
    pgdata.mkdir(parents=True)
    inst._write_hba("abcdefabcdef12", ["10.0.1.11/32"])
    lines = [ln.split() for ln in (pgdata / "pg_hba.conf").read_text().splitlines() if ln and not ln.startswith("#")]
    admin = [ln for ln in lines if ln[2] == "dmx_admin"]
    assert ["host", "all", "dmx_admin", "127.0.0.1/32", "scram-sha-256"] in admin
    assert admin[-1] == ["host", "all", "dmx_admin", "all", "reject"]
    restorers = [ln for ln in lines if ln[2] == "+dmx_restorers"]
    assert restorers[-1][-1] == "reject" and all(ln[3] != "10.0.1.11/32" for ln in restorers)
    assert ["host", "all", "all", "10.0.1.11/32", "scram-sha-256"] in lines
    assert lines[-1] == ["host", "all", "all", "::/0", "reject"]
    assert not any("trust" in ln and ln[0] == "host" for ln in lines)
    assert oct(os.stat(pgdata / "pg_hba.conf").st_mode & 0o777) == "0o600"


def test_dir_mode_volumes_and_delete(tmp_path: Path) -> None:
    s = Storage(str(tmp_path / "dp"), mode="dir")
    uid, gid = os.getuid(), os.getgid()
    v = s.ensure("abcdefabcdef12-sys", 384, max(uid, 1), max(gid, 1)) if uid else None
    if v is not None:  # chown to ourselves works without root
        assert v["hard_limit"] is False and v["image_size_mb"] == 384 and v["mounted"]
        (Path(v["path"]) / "f").write_bytes(b"x" * 8192)
        assert s.info("abcdefabcdef12-sys")["fs_used_bytes"] >= 8192
        assert s.grow("abcdefabcdef12-sys", 512)["image_size_mb"] == 512
        with pytest.raises(AgentError):
            s.grow("abcdefabcdef12-sys", 100)
        assert [x["instance_id"] for x in s.list()] == ["abcdefabcdef12-sys"]
        assert s.delete("abcdefabcdef12-sys")["deleted"] is True
        assert not Path(v["path"]).exists() and s.list() == []


def test_unknown_version_is_refused() -> None:
    with pytest.raises(AgentError) as ei:
        agent.Instances.bindir("99")
    assert ei.value.code == "unsupported_version"
    with pytest.raises(AgentError):
        agent.Instances.bindir("1/../../bin")

import os
import uuid

import pytest

from dmx_provisioner.backup_store import LocalBackupStore, S3BackupStore, object_key
from dmx_provisioner.config import Settings
from dmx_provisioner.validation import ValidationFailed


def test_object_key_only_from_uuids() -> None:
    d, b = str(uuid.uuid4()), str(uuid.uuid4())
    assert object_key(d, b) == f"{d}/{b}.dmxb"
    for bad in ("../../etc/passwd", "x", d.upper(), d + "/..", ""):
        with pytest.raises(ValidationFailed):
            object_key(bad, b)
        with pytest.raises(ValidationFailed):
            object_key(d, bad)


def _roundtrip(store) -> None:  # type: ignore[no-untyped-def]
    key = object_key(str(uuid.uuid4()), str(uuid.uuid4()))
    data = os.urandom(3 * 1024 * 1024 + 5)
    obj = store.put(key, (data[i : i + 65536] for i in range(0, len(data), 65536)))
    assert obj.size == len(data)
    assert store.exists(key)
    f = store.open(key)
    assert f.read() == data
    f.close()
    assert store.usage_bytes() >= len(data)
    assert store.delete(key) is True
    assert not store.exists(key)
    with pytest.raises(FileNotFoundError):
        store.open(key)


def test_local_store(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = LocalBackupStore(str(tmp_path))
    _roundtrip(store)


def test_local_store_failed_write_leaves_nothing(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = LocalBackupStore(str(tmp_path))
    key = object_key(str(uuid.uuid4()), str(uuid.uuid4()))

    def boom():  # type: ignore[no-untyped-def]
        yield b"partial"
        raise RuntimeError("dump failed")

    with pytest.raises(RuntimeError):
        store.put(key, boom())
    assert not store.exists(key)
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []


def test_local_store_files_are_private(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = LocalBackupStore(str(tmp_path))
    key = object_key(str(uuid.uuid4()), str(uuid.uuid4()))
    store.put(key, [b"x"])
    assert oct((tmp_path / key).stat().st_mode & 0o777) == "0o600"


@pytest.fixture
def s3_server():  # type: ignore[no-untyped-def]
    from moto.server import ThreadedMotoServer

    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0)
    server.start()
    host, port = server.get_host_and_port()
    yield f"http://{host}:{port}"
    server.stop()


def test_s3_store_against_emulator(s3_server, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import boto3

    s = Settings(
        DMX_BACKUP_STORE="s3",
        DMX_BACKUP_S3_ENDPOINT=s3_server,
        DMX_BACKUP_S3_BUCKET="dmx-backups",
        DMX_BACKUP_S3_ACCESS_KEY="test",
        DMX_BACKUP_S3_SECRET_KEY="test",
    )
    boto3.client(
        "s3", endpoint_url=s3_server, region_name="us-east-1", aws_access_key_id="t", aws_secret_access_key="t"
    ).create_bucket(Bucket="dmx-backups")
    store = S3BackupStore(s, str(tmp_path / "spool"))
    _roundtrip(store)
    assert list((tmp_path / "spool").iterdir()) == []  # spool cleaned up

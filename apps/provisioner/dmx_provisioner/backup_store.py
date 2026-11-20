"""Where encrypted backup objects live.

Object keys are derived only from validated UUIDs (`<database uuid>/<backup uuid>.dmxb`); clients
never send paths. Objects are written atomically (temp file + rename, or a completed upload) so a
crashed backup never leaves a half-written object under a final key.

- LocalBackupStore: a directory on the provisioner host (tested end to end).
- S3BackupStore: any S3-compatible API, e.g. OCI Object Storage's S3 compatibility endpoint
  (tested against an S3 emulator; not yet exercised against OCI itself).
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import shutil
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Protocol

from dmx_provisioner.config import Settings, get_settings
from dmx_provisioner.validation import ValidationFailed

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def object_key(database_id: str, backup_id: str) -> str:
    if not UUID_RE.match(database_id or "") or not UUID_RE.match(backup_id or ""):
        raise ValidationFailed("invalid backup identifier")
    return f"{database_id}/{backup_id}.dmxb"


@dataclass
class StoredObject:
    key: str
    reference: str
    size: int
    sha256: str


class BackupStore(Protocol):
    name: str

    def put(self, key: str, chunks: Iterable[bytes]) -> StoredObject: ...
    def open(self, key: str) -> IO[bytes]: ...
    def delete(self, key: str) -> bool: ...
    def exists(self, key: str) -> bool: ...
    def usage_bytes(self) -> int: ...


def _spool(chunks: Iterable[bytes], directory: str) -> tuple[str, int, str]:
    fd, tmp = tempfile.mkstemp(prefix=".part-", dir=directory)
    h = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(fd, "wb") as f:
            for c in chunks:
                f.write(c)
                h.update(c)
                size += len(c)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return tmp, size, h.hexdigest()


class LocalBackupStore:
    name = "local"

    def __init__(self, root: str):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents:
            raise ValidationFailed("invalid backup key")
        return p

    def put(self, key: str, chunks: Iterable[bytes]) -> StoredObject:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp, size, digest = _spool(chunks, str(path.parent))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        return StoredObject(key=key, reference=f"local:{key}", size=size, sha256=digest)

    def open(self, key: str) -> IO[bytes]:
        path = self._path(key)
        if not path.is_file():
            raise FileNotFoundError("backup object not found")
        return path.open("rb")

    def delete(self, key: str) -> bool:
        path = self._path(key)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        with contextlib.suppress(OSError):
            path.parent.rmdir()
        return True

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def usage_bytes(self) -> int:
        total = 0
        if not self.root.exists():
            return 0
        for dirpath, _dirs, files in os.walk(self.root):
            for f in files:
                try:
                    total += (Path(dirpath) / f).stat().st_size
                except OSError:
                    continue
        return total

    def free_bytes(self) -> int:
        self.root.mkdir(parents=True, exist_ok=True)
        return shutil.disk_usage(self.root).free


class S3BackupStore:
    name = "s3"

    def __init__(self, s: Settings, spool_dir: str):
        import boto3
        from botocore.config import Config

        assert s.s3_bucket and s.s3_access_key and s.s3_secret_key
        self.bucket = s.s3_bucket
        self.prefix = s.s3_prefix
        self.spool_dir = spool_dir
        self.client: Any = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint,
            region_name=s.s3_region,
            aws_access_key_id=s.s3_access_key.get_secret_value(),
            aws_secret_access_key=s.s3_secret_key.get_secret_value(),
            config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 5, "mode": "standard"}),
        )

    def _k(self, key: str) -> str:
        return self.prefix + key

    def put(self, key: str, chunks: Iterable[bytes]) -> StoredObject:
        # Spool the ciphertext locally first: gives us the size/checksum and a retryable multipart upload.
        Path(self.spool_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp, size, digest = _spool(chunks, self.spool_dir)
        try:
            with open(tmp, "rb") as f:
                self.client.upload_fileobj(f, self.bucket, self._k(key), ExtraArgs={"Metadata": {"sha256": digest}})
        finally:
            Path(tmp).unlink(missing_ok=True)
        return StoredObject(key=key, reference=f"s3:{self.bucket}/{self._k(key)}", size=size, sha256=digest)

    def open(self, key: str) -> IO[bytes]:
        from botocore.exceptions import ClientError

        try:
            return self.client.get_object(Bucket=self.bucket, Key=self._k(key))["Body"]  # type: ignore[no-any-return]
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                raise FileNotFoundError("backup object not found") from e
            raise

    def delete(self, key: str) -> bool:
        existed = self.exists(key)
        self.client.delete_object(Bucket=self.bucket, Key=self._k(key))
        return existed

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=self._k(key))
            return True
        except ClientError:
            return False

    def usage_bytes(self) -> int:
        total = 0
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=self.prefix):
            total += sum(int(o.get("Size", 0)) for o in page.get("Contents", []))
        return total


_store: BackupStore | None = None


def get_store() -> BackupStore:
    global _store
    if _store is None:
        s = get_settings()
        if s.backup_store == "s3":
            _store = S3BackupStore(s, os.path.join(s.backup_dir, ".spool"))
        else:
            _store = LocalBackupStore(s.backup_dir)
    return _store


def reset_store() -> None:
    global _store
    _store = None

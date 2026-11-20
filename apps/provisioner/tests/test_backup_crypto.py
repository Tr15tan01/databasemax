import io
import os
import struct

import pytest

from dmx_provisioner import backup_crypto as c

KEY = os.urandom(32)


def enc(data: bytes, parts: int = 7, key: bytes = KEY, kid: str = "k1") -> bytes:
    step = max(1, len(data) // parts)
    chunks = [data[i : i + step] for i in range(0, len(data), step)] or [b""]
    return b"".join(c.encrypt_stream(chunks, key, kid))


def dec(blob: bytes, keys: dict[str, bytes] | None = None) -> bytes:
    return b"".join(c.decrypt_stream(io.BytesIO(blob).read, keys or {"k1": KEY}))


@pytest.mark.parametrize("size", [0, 1, 1000, c.CHUNK - 1, c.CHUNK, c.CHUNK + 1, 3 * c.CHUNK + 17])
def test_roundtrip(size: int) -> None:
    data = os.urandom(size)
    blob = enc(data)
    assert dec(blob) == data
    if size >= 16:
        assert data[:64] not in blob  # ciphertext, not plaintext


def test_nonce_prefix_is_random_per_backup() -> None:
    data = b"same data" * 100
    assert enc(data) != enc(data)


def _chunks(blob: bytes) -> tuple[bytes, list[bytes]]:
    hdr_len = len(c.MAGIC) + 1 + blob[len(c.MAGIC)] + 8
    header, rest, out = blob[:hdr_len], blob[hdr_len:], []
    while rest:
        (n,) = struct.unpack(">I", rest[:4])
        out.append(rest[: 4 + n])
        rest = rest[4 + n :]
    return header, out


def test_bit_flip_rejected() -> None:
    blob = bytearray(enc(os.urandom(3 * c.CHUNK)))
    blob[len(blob) // 2] ^= 1
    with pytest.raises(c.BackupIntegrityError):
        dec(bytes(blob))


def test_truncation_at_chunk_boundary_rejected() -> None:
    header, chunks = _chunks(enc(os.urandom(3 * c.CHUNK)))
    assert len(chunks) == 3
    with pytest.raises(c.BackupIntegrityError, match="truncated"):
        dec(header + b"".join(chunks[:2]))


def test_reorder_rejected() -> None:
    header, chunks = _chunks(enc(os.urandom(3 * c.CHUNK)))
    with pytest.raises(c.BackupIntegrityError):
        dec(header + chunks[1] + chunks[0] + chunks[2])


def test_trailing_data_rejected() -> None:
    blob = enc(b"hello")
    with pytest.raises(c.BackupIntegrityError, match="after final"):
        dec(blob + b"x")


def test_header_tamper_rejected() -> None:
    blob = bytearray(enc(b"hello world"))
    blob[len(c.MAGIC) + 1 + 2 + 3] ^= 1  # inside the nonce prefix
    with pytest.raises(c.BackupIntegrityError):
        dec(bytes(blob))


def test_wrong_or_unknown_key() -> None:
    blob = enc(b"secret rows")
    with pytest.raises(c.BackupIntegrityError, match="not available"):
        dec(blob, {"other": KEY})
    with pytest.raises(c.BackupIntegrityError, match="authentication"):
        dec(blob, {"k1": os.urandom(32)})


def test_splice_between_backups_rejected() -> None:
    h1, c1 = _chunks(enc(os.urandom(2 * c.CHUNK)))
    _h2, c2 = _chunks(enc(os.urandom(2 * c.CHUNK)))
    with pytest.raises(c.BackupIntegrityError):
        dec(h1 + c1[0] + c2[1])


def test_not_a_backup() -> None:
    with pytest.raises(c.BackupIntegrityError, match="not a DatabaseMax backup"):
        dec(b"PGDMP....")


def test_key_validation() -> None:
    with pytest.raises(ValueError):
        list(c.encrypt_stream([b"x"], b"short", "k1"))


def test_context_binding() -> None:
    blob = b"".join(c.encrypt_stream([b"rows"], KEY, "k1", context=b"db-a/backup-1"))
    assert blob.startswith(b"DMXB2")
    assert b"".join(c.decrypt_stream(io.BytesIO(blob).read, {"k1": KEY}, context=b"db-a/backup-1")) == b"rows"
    with pytest.raises(c.BackupIntegrityError):
        b"".join(c.decrypt_stream(io.BytesIO(blob).read, {"k1": KEY}, context=b"db-b/backup-9"))


def test_legacy_format_still_readable() -> None:
    import struct as st

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    prefix = os.urandom(8)
    header = b"DMXB1" + bytes([2]) + b"k1" + prefix
    ct = AESGCM(KEY).encrypt(prefix + st.pack(">I", 0), b"old", header + st.pack(">IB", 0, 1))
    blob = header + st.pack(">I", len(ct)) + ct
    assert dec(blob) == b"old"

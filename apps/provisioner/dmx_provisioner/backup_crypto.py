"""Streaming authenticated encryption for backups (format "DMXB1").

Built only from standard primitives (AES-256-GCM from `cryptography`) in the well-known
chunked-AEAD ("STREAM") construction:

  header  = b"DMXB2" | u8 len(key_id) | key_id | 8-byte random nonce prefix
  chunk_i = u32 len | AES-GCM(key, nonce = prefix || u32 i, plaintext_i,
                              aad = header || u32 i || u8 final || context)

`context` is "<database id>/<backup id>": it is not stored in the object, the reader supplies
the value it expects, so an object can't be substituted for a different backup. Format DMXB1
(no context) is still readable for objects written before the context was introduced.

- Every chunk is authenticated, and the header (including the key id) is bound into every chunk.
- The chunk counter in the nonce/AAD prevents reordering, duplication and splicing.
- The `final` flag on the last chunk prevents truncation: a stream that ends without an
  authenticated final chunk, or continues after it, is rejected.
- A fresh random nonce prefix per backup keeps (key, nonce) pairs unique.
"""

from __future__ import annotations

import os
import struct
from collections.abc import Callable, Iterable, Iterator

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"DMXB2"
LEGACY_MAGIC = b"DMXB1"
CHUNK = 1024 * 1024
TAG = 16

Reader = Callable[[int], bytes]


class BackupIntegrityError(Exception):
    pass


def _aad(header: bytes, i: int, final: bool, context: bytes = b"") -> bytes:
    return header + struct.pack(">IB", i, 1 if final else 0) + context


def _nonce(prefix: bytes, i: int) -> bytes:
    return prefix + struct.pack(">I", i)


def _blocks(chunks: Iterable[bytes]) -> Iterator[bytes]:
    buf = bytearray()
    for data in chunks:
        buf += data
        while len(buf) >= CHUNK:
            yield bytes(buf[:CHUNK])
            del buf[:CHUNK]
    if buf:
        yield bytes(buf)


def encrypt_stream(chunks: Iterable[bytes], key: bytes, key_id: str, *, context: bytes = b"") -> Iterator[bytes]:
    if len(key) != 32:
        raise ValueError("backup key must be 32 bytes")
    kid = key_id.encode()
    if not 1 <= len(kid) <= 64:
        raise ValueError("invalid key id")
    prefix = os.urandom(8)
    header = MAGIC + bytes([len(kid)]) + kid + prefix
    aes = AESGCM(key)
    yield header

    def seal(i: int, block: bytes, final: bool) -> bytes:
        ct = aes.encrypt(_nonce(prefix, i), block, _aad(header, i, final, context))
        return struct.pack(">I", len(ct)) + ct

    i = 0
    prev: bytes | None = None
    for block in _blocks(chunks):  # one-block look-ahead so the last block is flagged final
        if prev is not None:
            yield seal(i, prev, False)
            i += 1
        prev = block
    yield seal(i, prev if prev is not None else b"", True)


def read_key_id(read: Reader) -> tuple[bytes, str, bytes]:
    magic = read(len(MAGIC))
    if magic not in (MAGIC, LEGACY_MAGIC):
        raise BackupIntegrityError("not a DatabaseMax backup")
    n = read(1)
    if len(n) != 1 or n[0] == 0:
        raise BackupIntegrityError("truncated header")
    kid = read(n[0])
    prefix = read(8)
    if len(kid) != n[0] or len(prefix) != 8:
        raise BackupIntegrityError("truncated header")
    try:
        return magic + n + kid + prefix, kid.decode(), prefix
    except UnicodeDecodeError as e:
        raise BackupIntegrityError("invalid header") from e


def _read_exact(read: Reader, n: int) -> bytes:
    out = bytearray()
    while len(out) < n:
        part = read(n - len(out))
        if not part:
            break
        out += part
    return bytes(out)


def decrypt_stream(read: Reader, keys: dict[str, bytes], *, context: bytes = b"") -> Iterator[bytes]:
    """`read(n)` returns up to n bytes (file-like). Yields authenticated plaintext blocks.

    Raises BackupIntegrityError on tampering, truncation, trailing data or an unknown key.
    Callers must treat everything yielded as provisional until the generator finishes
    without raising (restore targets a fresh database which is destroyed on failure).
    """
    header, kid, prefix = read_key_id(lambda n: _read_exact(read, n))
    if header.startswith(LEGACY_MAGIC):
        context = b""  # legacy objects carry no context binding
    key = keys.get(kid)
    if key is None:
        raise BackupIntegrityError(f"backup key {kid!r} is not available")
    aes = AESGCM(key)
    i = 0
    while True:
        raw_len = _read_exact(read, 4)
        if not raw_len:
            raise BackupIntegrityError("backup is truncated (no final chunk)")
        if len(raw_len) != 4:
            raise BackupIntegrityError("truncated chunk header")
        (n,) = struct.unpack(">I", raw_len)
        if n < TAG or n > CHUNK + TAG:
            raise BackupIntegrityError("invalid chunk length")
        ct = _read_exact(read, n)
        if len(ct) != n:
            raise BackupIntegrityError("truncated chunk")
        nonce = _nonce(prefix, i)
        final = False
        try:
            pt = aes.decrypt(nonce, ct, _aad(header, i, False, context))
        except InvalidTag:
            try:
                pt = aes.decrypt(nonce, ct, _aad(header, i, True, context))
                final = True
            except InvalidTag as e:
                raise BackupIntegrityError("authentication failed (corrupted or tampered backup)") from e
        if final:
            if _read_exact(read, 1):
                raise BackupIntegrityError("unexpected data after final chunk")
            yield pt
            return
        yield pt
        i += 1

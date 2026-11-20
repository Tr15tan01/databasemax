"""Minimal PostgreSQL wire-protocol helpers (only what the gateway needs)."""

from __future__ import annotations

import struct

SSL_REQUEST = 80877103
GSSENC_REQUEST = 80877104
CANCEL_REQUEST = 80877102
PROTOCOL_3_0 = 196608
MAX_STARTUP_LEN = 10_000


class ProtocolError(Exception):
    pass


def parse_startup_params(payload: bytes) -> dict[str, str]:
    """`payload` is the StartupMessage body after the protocol version."""
    parts = payload.split(b"\x00")
    params: dict[str, str] = {}
    it = iter(parts)
    for key in it:
        if key == b"":
            break
        try:
            value = next(it)
        except StopIteration as e:
            raise ProtocolError("malformed startup message") from e
        params[key.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    return params


def error_response(message: str, code: str = "08004", severity: str = "FATAL") -> bytes:
    fields = b""
    for t, v in ((b"S", severity), (b"V", severity), (b"C", code), (b"M", message)):
        fields += t + v.encode() + b"\x00"
    fields += b"\x00"
    return b"E" + struct.pack("!I", len(fields) + 4) + fields


def pack_startup(protocol: int, body: bytes) -> bytes:
    return struct.pack("!II", len(body) + 8, protocol) + body

"""SCRAM-SHA-256 verifier generation (RFC 5802 / RFC 7677), in PostgreSQL's storage format.

This is not custom cryptography: it composes standard primitives (PBKDF2-HMAC-SHA256, HMAC,
SHA-256) exactly as PostgreSQL does in `scram_build_secret`, so PostgreSQL receives only the
verifier and never the plaintext password. Correctness is covered by an integration test that
logs in to a real PostgreSQL with the password after setting this verifier.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

DEFAULT_ITERATIONS = 4096


def scram_sha256_verifier(password: str, *, iterations: int = DEFAULT_ITERATIONS, salt: bytes | None = None) -> str:
    if not password.isascii():
        raise ValueError("only ASCII passwords are supported (SASLprep not applied)")
    salt = salt or os.urandom(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"

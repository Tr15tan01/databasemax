"""Opaque random tokens. Only SHA-256 hashes are persisted (tokens are 256-bit random, so a
fast hash is appropriate and allows indexed lookup)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import string


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def constant_time_equals(a: str | bytes, b: str | bytes) -> bool:
    if isinstance(a, str):
        a = a.encode()
    if isinstance(b, str):
        b = b.encode()
    return hmac.compare_digest(a, b)


_ALNUM = string.ascii_letters + string.digits
_LOWER_ALNUM = string.ascii_lowercase + string.digits


def random_password(length: int = 32) -> str:
    """Database password: alphanumeric only (no SASLprep or URL-escaping surprises)."""
    while True:
        pw = "".join(secrets.choice(_ALNUM) for _ in range(length))
        if any(c.islower() for c in pw) and any(c.isupper() for c in pw) and any(c.isdigit() for c in pw):
            return pw


_BASE32 = "abcdefghijkmnpqrstuvwxyz23456789"  # no l, o, 0, 1


def random_id(length: int = 10) -> str:
    return "".join(secrets.choice(_BASE32) for _ in range(length))


def random_lower_alnum(length: int) -> str:
    return "".join(secrets.choice(_LOWER_ALNUM) for _ in range(length))

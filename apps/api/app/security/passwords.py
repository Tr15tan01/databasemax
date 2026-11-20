"""Password hashing (Argon2id via argon2-cffi) and password policy."""

from __future__ import annotations

import contextlib
from functools import lru_cache

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings

MIN_LENGTH = 10
MAX_LENGTH = 128

# A small deny-list of extremely common passwords (length >= MIN_LENGTH). A breach-corpus check
# (k-anonymity HIBP API) can be added behind a config flag later.
_COMMON = {
    "password12",
    "password123",
    "1234567890",
    "qwertyuiop",
    "iloveyou12",
    "12345678910",
    "passw0rd123",
    "administrator",
    "welcome123",
    "qwerty1234",
    "letmein123",
    "databasemax",
    "postgres123",
    "changeme123",
    "abcdefghij",
    "0987654321",
    "1q2w3e4r5t",
    "password1!",
}


@lru_cache
def _hasher() -> PasswordHasher:
    s = get_settings()
    return PasswordHasher(
        time_cost=s.argon2_time_cost,
        memory_cost=s.argon2_memory_kib,
        parallelism=s.argon2_parallelism,
        hash_len=32,
        salt_len=16,
        type=Type.ID,
    )


def hash_password(password: str) -> str:
    return _hasher().hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    if not password_hash:
        # Burn comparable time so timing doesn't reveal "no password set / no account".
        dummy_verify()
        return False
    try:
        return _hasher().verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


_DUMMY_HASH: str | None = None


def dummy_verify() -> None:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = _hasher().hash("dummy-password-for-timing")
    with contextlib.suppress(VerifyMismatchError):
        _hasher().verify(_DUMMY_HASH, "not-the-password")


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher().check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def password_problems(password: str, email: str | None = None) -> list[str]:
    problems: list[str] = []
    if len(password) < MIN_LENGTH:
        problems.append(f"Password must be at least {MIN_LENGTH} characters.")
    if len(password) > MAX_LENGTH:
        problems.append(f"Password must be at most {MAX_LENGTH} characters.")
    if len(set(password)) < 4:
        problems.append("Password is too repetitive.")
    if password.lower() in _COMMON:
        problems.append("Password is too common.")
    if email:
        local = email.split("@", 1)[0].lower()
        if len(local) >= 4 and local in password.lower():
            problems.append("Password must not contain your email name.")
    return problems

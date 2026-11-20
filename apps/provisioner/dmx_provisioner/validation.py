"""Strict validation for every value that reaches the host agent or SQL."""

from __future__ import annotations

import re

INSTANCE_ID = re.compile(r"^[a-z0-9]{12,32}$")
PG_IDENT = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
SCRAM = re.compile(r"^SCRAM-SHA-256\$\d{4,6}:[A-Za-z0-9+/=]{16,64}\$[A-Za-z0-9+/=]{40,64}:[A-Za-z0-9+/=]{40,64}$")
RESERVED_DBS = {"postgres", "template0", "template1"}
RESERVED_ROLE_PREFIXES = ("pg_", "dmx_")
RESERVED_ROLES = {"postgres", "public", "dmx_admin"}


class ValidationFailed(ValueError):
    pass


def instance_id(v: str) -> str:
    if not INSTANCE_ID.match(v or ""):
        raise ValidationFailed("invalid instance id")
    return v


def db_name(v: str) -> str:
    if not PG_IDENT.match(v or "") or v in RESERVED_DBS:
        raise ValidationFailed("invalid database name")
    return v


def role_name(v: str) -> str:
    if not PG_IDENT.match(v or "") or v in RESERVED_ROLES or v.startswith(RESERVED_ROLE_PREFIXES):
        raise ValidationFailed("invalid role name")
    return v


def scram(v: str) -> str:
    if not SCRAM.match(v or ""):
        raise ValidationFailed("invalid SCRAM verifier")
    return v

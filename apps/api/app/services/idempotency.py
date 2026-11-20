"""Idempotency-Key support for creation endpoints.

Same key + same request body -> the stored response is replayed (no duplicate resources).
Same key + different body -> 422. Concurrent duplicate while the first is in flight -> 409.
Keys are scoped per user and endpoint and kept for 24 hours.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ApiError, Conflict
from app.models import IdempotencyKey, utcnow

TTL = dt.timedelta(hours=24)


def _hash(body: dict[str, Any]) -> bytes:
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).digest()


def validate_key(key: str | None) -> str | None:
    if key is None:
        return None
    key = key.strip()
    if not 8 <= len(key) <= 128 or not all(c.isalnum() or c in "-_:." for c in key):
        raise ApiError(
            "Idempotency-Key must be 8-128 characters of [A-Za-z0-9-_:.].",
            code="invalid_idempotency_key",
            status_code=400,
        )
    return key


def begin(
    db: Session, user_id: uuid.UUID, key: str | None, endpoint: str, body: dict[str, Any]
) -> tuple[IdempotencyKey | None, tuple[int, dict[str, Any]] | None]:
    key = validate_key(key)
    if key is None:
        return None, None
    h = _hash(body)
    existing = db.scalar(
        select(IdempotencyKey).where(
            IdempotencyKey.user_id == user_id, IdempotencyKey.key == key, IdempotencyKey.endpoint == endpoint
        )
    )
    if existing is not None and existing.created_at < utcnow() - TTL:
        db.delete(existing)
        db.flush()
        existing = None
    if existing is not None:
        if existing.request_hash != h:
            raise ApiError(
                "This Idempotency-Key was already used with a different request.",
                code="idempotency_key_reused",
                status_code=422,
            )
        if existing.response_status is None:
            raise Conflict("A request with this Idempotency-Key is still in progress.", code="idempotency_in_progress")
        return existing, (existing.response_status, existing.response_body or {})
    rec = IdempotencyKey(id=uuid.uuid4(), user_id=user_id, key=key, endpoint=endpoint, request_hash=h)
    try:
        with db.begin_nested():
            db.add(rec)
            db.flush()
    except IntegrityError as e:
        raise Conflict(
            "A request with this Idempotency-Key is still in progress.", code="idempotency_in_progress"
        ) from e
    return rec, None


def complete(rec: IdempotencyKey | None, status: int, body: dict[str, Any]) -> None:
    if rec is not None:
        rec.response_status = status
        rec.response_body = json.loads(json.dumps(body, default=str))


def prune(db: Session) -> int:
    r = db.execute(delete(IdempotencyKey).where(IdempotencyKey.created_at < utcnow() - TTL))
    return int(r.rowcount or 0)  # type: ignore[attr-defined]

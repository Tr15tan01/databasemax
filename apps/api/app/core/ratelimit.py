"""Redis-backed rate limiting (fixed windows) and progressive login throttling.

If Redis is unavailable, sensitive endpoints fail closed (503) rather than silently allowing
unlimited attempts.
"""

from __future__ import annotations

import hashlib
import math
import time

import redis

from app.core.config import get_settings
from app.core.errors import RateLimited, ServiceUnavailable
from app.core.redis import get_redis


def _k(*parts: str) -> str:
    return "dmx:rl:" + ":".join(parts)


def hash_ident(value: str) -> str:
    return hashlib.sha256(value.lower().encode()).hexdigest()[:32]


def hit(bucket: str, ident: str, limit: int, window_seconds: int, *, fail_closed: bool = True) -> None:
    """Count one request; raise RateLimited when above `limit` within the window."""
    if not get_settings().rate_limit_enabled:
        return
    window = int(time.time() // window_seconds)
    key = _k(bucket, ident, str(window))
    try:
        r = get_redis()
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_seconds + 5)
        count, _ = pipe.execute()
    except redis.RedisError as e:
        if fail_closed:
            raise ServiceUnavailable("Temporarily unable to process this request.") from e
        return
    if int(count) > limit:
        retry = window_seconds - int(time.time() % window_seconds)
        raise RateLimited("Too many requests. Please try again later.", headers={"Retry-After": str(max(retry, 1))})


# --- progressive login throttling ---------------------------------------------------------------
# After 5 consecutive failures for an account, further attempts are blocked for an exponentially
# growing period (30s, 60s, 120s, ... capped at 1h). A success resets the counter.

FREE_FAILURES = 5


def login_block_seconds(email: str) -> int:
    if not get_settings().rate_limit_enabled:
        return 0
    try:
        r = get_redis()
        until = r.get(_k("login-block", hash_ident(email)))
    except redis.RedisError as e:
        raise ServiceUnavailable("Temporarily unable to process this request.") from e
    if until and float(until) > time.time():
        return math.ceil(float(until) - time.time())
    return 0


def record_login_failure(email: str) -> None:
    if not get_settings().rate_limit_enabled:
        return
    ident = hash_ident(email)
    r = get_redis()
    failures = int(r.incr(_k("login-fail", ident)))
    r.expire(_k("login-fail", ident), 24 * 3600)
    if failures >= FREE_FAILURES:
        delay = min(30 * 2 ** (failures - FREE_FAILURES), 3600)
        r.set(_k("login-block", ident), str(time.time() + delay), ex=delay + 5)


def reset_login_failures(email: str) -> None:
    try:
        r = get_redis()
        ident = hash_ident(email)
        r.delete(_k("login-fail", ident), _k("login-block", ident))
    except redis.RedisError:
        pass


def login_failure_count(email: str) -> int:
    try:
        v = get_redis().get(_k("login-fail", hash_ident(email)))
        return int(v or 0)
    except redis.RedisError:
        return 0

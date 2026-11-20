"""Bounded concurrency backed by Redis sorted sets.

Each holder adds a random token scored by its expiry time; expired tokens are dropped on every
acquire, so a crashed process can't leak a slot for longer than its TTL. Fails closed: if Redis
is unavailable the work is refused rather than run unbounded.
"""

from __future__ import annotations

import contextlib
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import redis

from app.core.errors import ApiError, ServiceUnavailable
from app.core.redis import get_redis

_ACQUIRE = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[3]) then return 0 end
redis.call('ZADD', KEYS[1], ARGV[2], ARGV[4])
redis.call('EXPIRE', KEYS[1], ARGV[5])
return 1
"""


@contextmanager
def slot(key: str, limit: int, ttl_seconds: float, *, busy_message: str, busy_code: str = "busy") -> Iterator[None]:
    token = uuid.uuid4().hex
    now = time.time()
    try:
        r = get_redis()
        ok = r.eval(_ACQUIRE, 1, "dmx:slots:" + key, now, now + ttl_seconds, limit, token, int(ttl_seconds) + 5)
    except redis.RedisError as e:
        raise ServiceUnavailable("Temporarily unable to process this request.") from e
    if not ok:
        raise ApiError(busy_message, code=busy_code, status_code=429, headers={"Retry-After": "2"})
    try:
        yield
    finally:
        with contextlib.suppress(redis.RedisError):  # the token expires on its own
            r.zrem("dmx:slots:" + key, token)

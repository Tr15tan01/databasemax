from __future__ import annotations

from functools import lru_cache

import redis

from app.core.config import get_settings


@lru_cache
def get_redis() -> redis.Redis:
    return redis.Redis.from_url(
        get_settings().redis_url.get_secret_value(),
        socket_timeout=3,
        socket_connect_timeout=3,
        decode_responses=True,
        health_check_interval=30,
    )


JOB_WAKEUP_KEY = "dmx:jobs:wakeup"


def wake_workers() -> None:
    try:
        r = get_redis()
        r.lpush(JOB_WAKEUP_KEY, "1")
        r.ltrim(JOB_WAKEUP_KEY, 0, 99)
    except redis.RedisError:
        # Jobs live in PostgreSQL; the worker also polls, so a missed wake-up only adds latency.
        pass

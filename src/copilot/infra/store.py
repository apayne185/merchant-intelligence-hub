"""
Shared Redis client for the rate limiter and response cache (D53).

Short socket timeouts on purpose: both callers fail *open* when Redis is
unreachable (a Redis blip degrades rate limiting/caching, it must not take
/ask down with it), so a slow Redis has to fail fast rather than add
seconds of latency to every request while it times out.
"""
from __future__ import annotations

from functools import lru_cache

import redis


@lru_cache(maxsize=4)
def redis_client(url: str) -> redis.Redis:
    return redis.Redis.from_url(
        url,
        socket_timeout=0.25,
        socket_connect_timeout=0.25,
        health_check_interval=30,
        decode_responses=True,
    )


def ping(url: str | None) -> str:
    """For /ready: "disabled" | "ok" | "unavailable"."""
    if not url:
        return "disabled"
    try:
        return "ok" if redis_client(url).ping() else "unavailable"
    except redis.RedisError:
        return "unavailable"

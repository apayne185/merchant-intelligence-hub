"""
Per-caller fixed-window rate limiting for /ask (D53).

Keyed by the JWT subject when authenticated (a tenant's quota follows the
tenant across IPs/replicas), by client IP otherwise. Redis-backed when
REDIS_URL is set — the only way a limit means anything with >1 replica
behind the HPA — with an in-process fallback for local dev/tests.

Fixed window, not sliding/token-bucket: one INCR+EXPIRE per request, exact
and cheap, and the known weakness (up to 2x the limit across a window
boundary) is an acceptable trade for an abuse/cost guard. It isn't
metering anything billed.

Fails open on Redis errors (logged + counted in
copilot_guardrail_events_total{event="ratelimit_backend_error"}): a Redis
outage should degrade abuse protection, not turn into a full /ask outage.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Annotated, Protocol

import redis
from fastapi import Depends, HTTPException, Request, Response, status
from src.copilot.infra.auth import Principal, get_principal
from src.copilot.infra.metrics import GUARDRAIL_EVENTS
from src.copilot.infra.settings import Settings, get_settings
from src.copilot.infra.store import redis_client

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 60


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    reset_after: int  # seconds until the current window ends


class RateLimiter(Protocol):
    def hit(self, key: str, limit: int, window: int = WINDOW_SECONDS) -> RateLimitResult: ...


def _window(window: int, now: float) -> tuple[int, int]:
    idx = int(now // window)
    reset_after = max(1, math.ceil((idx + 1) * window - now))
    return idx, reset_after


class InMemoryRateLimiter:
    """Single-process fallback. Old windows are pruned on every call, so
    memory is bounded by the number of distinct callers per window."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[tuple[str, int], int] = {}

    def hit(self, key: str, limit: int, window: int = WINDOW_SECONDS) -> RateLimitResult:
        idx, reset_after = _window(window, time.time())
        with self._lock:
            for k in [k for k in self._counts if k[1] < idx]:
                del self._counts[k]
            count = self._counts.get((key, idx), 0) + 1
            self._counts[(key, idx)] = count
        return RateLimitResult(count <= limit, limit, max(0, limit - count), reset_after)


class RedisRateLimiter:
    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    def hit(self, key: str, limit: int, window: int = WINDOW_SECONDS) -> RateLimitResult:
        idx, reset_after = _window(window, time.time())
        rkey = f"ratelimit:{key}:{idx}"
        try:
            pipe = self._client.pipeline(transaction=True)
            pipe.incr(rkey)
            # Key is unique per window, so re-arming the TTL on every hit is
            # harmless — it only ever outlives its window by < 1 window.
            pipe.expire(rkey, window * 2)
            count = int(pipe.execute()[0])
        except redis.RedisError:
            logger.warning("rate limiter backend unavailable; failing open", exc_info=True)
            GUARDRAIL_EVENTS.labels(event="ratelimit_backend_error").inc()
            return RateLimitResult(True, limit, limit, reset_after)
        return RateLimitResult(count <= limit, limit, max(0, limit - count), reset_after)


_IN_MEMORY = InMemoryRateLimiter()


def get_rate_limiter(settings: Annotated[Settings, Depends(get_settings)]) -> RateLimiter:
    if settings.redis_url:
        return RedisRateLimiter(redis_client(settings.redis_url))
    return _IN_MEMORY


def enforce_rate_limit(
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Principal:
    """Dependency for rate-limited endpoints. Runs after auth (so a 401
    doesn't burn quota) and returns the principal, so an endpoint needs
    only this one dependency for both."""
    limit = settings.rate_limit_per_minute
    if limit == 0:
        return principal
    if principal.is_anonymous:
        # request.client is the real caller only if uvicorn runs with
        # --proxy-headers and a trusted --forwarded-allow-ips (Dockerfile
        # CMD / k8s deployment) — otherwise every request behind the
        # ingress would share the ingress pod's IP and one bucket.
        key = f"ip:{request.client.host if request.client else 'unknown'}"
    else:
        key = f"sub:{principal.subject}"

    result = limiter.hit(key, limit)
    headers = {
        "X-RateLimit-Limit": str(result.limit),
        "X-RateLimit-Remaining": str(result.remaining),
        "X-RateLimit-Reset": str(result.reset_after),
    }
    if not result.allowed:
        GUARDRAIL_EVENTS.labels(event="rate_limited").inc()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate_limit_exceeded",
            headers={**headers, "Retry-After": str(result.reset_after)},
        )
    response.headers.update(headers)
    return principal

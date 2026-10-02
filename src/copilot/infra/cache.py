"""
/ask response cache: Redis when REDIS_URL is set, a small in-process TTL
cache otherwise. Disabled unless CACHE_TTL_SECONDS > 0.

The key includes the fact store's data version, which increments whenever
the streaming ingester applies a new filing, so an answer cached before a
10-Q landed is never served after it. It is built from the redacted
question (PII never becomes part of a Redis key) and the app version, so a
deploy that changes answers invalidates old entries without a flush.

Not keyed by caller: no tool applies per-caller authorization, so a cached
answer is equally valid for every caller. Tenant-scoped data would require
the tenant id in the key first.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Protocol

import redis
from src.copilot.infra.metrics import CACHE_EVENTS
from src.copilot.infra.store import redis_client

logger = logging.getLogger(__name__)


def cache_key(*, question: str, context: dict[str, Any], locale: str, mode: str, version: str,
              data_version: int) -> str:
    payload = json.dumps(
        {"q": question.strip(), "ctx": context, "l": locale, "mode": mode, "v": version, "d": data_version},
        sort_keys=True,
    )
    return "askcache:" + hashlib.sha256(payload.encode()).hexdigest()


class ResponseCache(Protocol):
    def get(self, key: str) -> dict[str, Any] | None: ...
    def set(self, key: str, value: dict[str, Any], ttl: int) -> None: ...


class InMemoryCache:
    def __init__(self, max_entries: int = 512) -> None:
        self._lock = threading.Lock()
        self._data: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self._max = max_entries

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._data.get(key)
            if item is None or item[0] < time.monotonic():
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return item[1]

    def set(self, key: str, value: dict[str, Any], ttl: int) -> None:
        with self._lock:
            self._data[key] = (time.monotonic() + ttl, value)
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)


class RedisCache:
    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    def get(self, key: str) -> dict[str, Any] | None:
        try:
            raw = self._client.get(key)
        except redis.RedisError:
            logger.warning("response cache unavailable on get", exc_info=True)
            CACHE_EVENTS.labels(result="error").inc()
            return None
        return json.loads(raw) if raw else None

    def set(self, key: str, value: dict[str, Any], ttl: int) -> None:
        try:
            self._client.set(key, json.dumps(value), ex=ttl)
        except redis.RedisError:
            logger.warning("response cache unavailable on set", exc_info=True)
            CACHE_EVENTS.labels(result="error").inc()


_IN_MEMORY = InMemoryCache()


def get_cache(redis_url: str | None) -> ResponseCache:
    return RedisCache(redis_client(redis_url)) if redis_url else _IN_MEMORY

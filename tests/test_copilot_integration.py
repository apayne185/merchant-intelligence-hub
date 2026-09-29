"""
Integration tests against real Redis and Postgres — the backends
tests/test_copilot_platform.py replaces with fakes.

Skipped unless the env vars are set; CI's `integration` job provides both as
service containers, and `docker compose up redis postgres` does locally:

    REDIS_URL=redis://localhost:6379/15 \
    AUDIT_DATABASE_URL=postgresql://copilot:copilot@localhost:5432/copilot \
    MOCK_LLM=1 uv run pytest -v -m integration
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.integration

REDIS_URL = os.environ.get("REDIS_URL")
DB_URL = os.environ.get("AUDIT_DATABASE_URL")


@pytest.mark.skipif(not REDIS_URL, reason="REDIS_URL not set")
def test_redis_rate_limiter_and_cache() -> None:
    from src.copilot.infra.cache import RedisCache
    from src.copilot.infra.ratelimit import RedisRateLimiter
    from src.copilot.infra.store import ping, redis_client

    assert ping(REDIS_URL) == "ok"
    client = redis_client(REDIS_URL)
    key = f"it-{uuid.uuid4().hex}"
    limiter = RedisRateLimiter(client)
    assert [limiter.hit(key, 2).allowed for _ in range(3)] == [True, True, False]
    ttl = client.ttl(next(iter(client.scan_iter(f"ratelimit:{key}:*"))))
    assert 0 < ttl <= 120

    cache = RedisCache(client)
    cache.set(f"askcache:{key}", {"answer": "x"}, ttl=30)
    assert cache.get(f"askcache:{key}") == {"answer": "x"}


@pytest.mark.skipif(not DB_URL, reason="AUDIT_DATABASE_URL not set")
def test_postgres_audit_sink_writes_row() -> None:
    import psycopg
    from src.copilot.infra.audit import AuditRecord, PostgresAuditSink

    sink = PostgresAuditSink(DB_URL)
    request_id = uuid.uuid4().hex
    try:
        assert sink.health() == "ok"
        sink.write(
            AuditRecord(
                request_id=request_id,
                trace_id="0" * 32,
                subject="it-user",
                outcome="ok",
                status_code=200,
                mode="mock",
                latency_ms=12,
                question_sha256="ab" * 32,
                route=["risk", "grounding"],
                pii_redactions={"card": 1},
            )
        )
    finally:
        sink.close()

    with psycopg.connect(DB_URL) as conn:
        row = conn.execute(
            "SELECT subject, route, pii_redactions FROM copilot_audit_log WHERE request_id = %s",
            (request_id,),
        ).fetchone()
    assert row == ("it-user", ["risk", "grounding"], {"card": 1})


@pytest.mark.skipif(not DB_URL, reason="AUDIT_DATABASE_URL not set")
def test_postgres_audit_concurrent_first_writes_all_land() -> None:
    """Regression: concurrent first writes raced on CREATE TABLE IF NOT
    EXISTS and one row was silently dropped (found in docker-compose)."""
    from concurrent.futures import ThreadPoolExecutor

    import psycopg
    from src.copilot.infra.audit import AuditRecord, PostgresAuditSink

    with psycopg.connect(DB_URL, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS copilot_audit_log")
    sinks = [PostgresAuditSink(DB_URL) for _ in range(4)]  # 4 "replicas"
    ids = [uuid.uuid4().hex for _ in range(8)]

    def write(i: int) -> None:
        sinks[i % 4].write(
            AuditRecord(
                request_id=ids[i], trace_id=None, subject="race", outcome="ok",
                status_code=200, mode="mock", latency_ms=1, question_sha256="0" * 64,
            )
        )

    try:
        with ThreadPoolExecutor(8) as ex:
            list(ex.map(write, range(8)))
    finally:
        for s in sinks:
            s.close()
    with psycopg.connect(DB_URL) as conn:
        n = conn.execute(
            "SELECT count(*) FROM copilot_audit_log WHERE request_id = ANY(%s)", (ids,)
        ).fetchone()[0]
    assert n == 8

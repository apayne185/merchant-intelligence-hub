"""Redis Streams ingestion: publication dedup, consumer-group delivery, reclaim, DLQ, broadcast."""
from __future__ import annotations

import json

import fakeredis
import pytest
from src.copilot.infra.cache import cache_key
from src.filings.factstore import FactStore
from src.streaming.events import DLQ_STREAM, FACTS_STREAM, FILINGS_STREAM, INGEST_GROUP, FilingEvent
from src.streaming.poller import INDEX_PATH, events_from_index, publish
from src.streaming.subscriber import FactSubscriber
from src.streaming.worker import IngestWorker, _lag_seconds, fixture_source


@pytest.fixture
def r() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis()


@pytest.fixture
def events() -> list[FilingEvent]:
    return events_from_index(json.loads(INDEX_PATH.read_text()))


def test_publication_is_deduplicated_across_pollers(r, events) -> None:
    assert publish(r, events) == len(events)
    assert publish(r, events) == 0  # restart / second poller: nothing republished
    assert r.xlen(FILINGS_STREAM) == len(events)


def test_event_round_trip(events) -> None:
    ev = events[0]
    assert FilingEvent.from_fields({k.encode(): v.encode() for k, v in ev.to_fields().items()}) == ev


def test_worker_ingests_and_broadcasts(r, events) -> None:
    publish(r, events[:5])
    worker = IngestWorker(r, fixture_source(), "w1", block_ms=1)
    assert worker.run_once() == {"ok": 5}
    assert r.xlen(FACTS_STREAM) == 5
    assert r.xpending(FILINGS_STREAM, INGEST_GROUP)["pending"] == 0  # all acknowledged


def test_competing_consumers_share_work(r, events) -> None:
    publish(r, events[:10])
    a = IngestWorker(r, fixture_source(), "a", batch=4, block_ms=1)
    b = IngestWorker(r, fixture_source(), "b", batch=4, block_ms=1)
    done = a.run_once().get("ok", 0) + b.run_once().get("ok", 0) + a.run_once().get("ok", 0)
    assert done == 10 and r.xlen(FACTS_STREAM) == 10  # each filing processed exactly once


def test_dead_consumer_messages_are_reclaimed(r, events) -> None:
    publish(r, events[:3])
    r.xgroup_create(FILINGS_STREAM, INGEST_GROUP, id="0", mkstream=True)
    r.xreadgroup(INGEST_GROUP, "crashed", {FILINGS_STREAM: ">"}, count=3)  # delivered, never acked
    survivor = IngestWorker(r, fixture_source(), "survivor", claim_idle_ms=0, block_ms=1)
    assert survivor.run_once() == {"ok": 3}
    assert r.xpending(FILINGS_STREAM, INGEST_GROUP)["pending"] == 0


def test_poison_message_goes_to_dlq_after_max_deliveries(r, events) -> None:
    publish(r, events[:1])

    def broken(_ev: FilingEvent) -> list:
        raise RuntimeError("SEC returned malformed JSON")

    worker = IngestWorker(r, broken, "w", claim_idle_ms=0, max_deliveries=3, block_ms=1)
    outcomes = [worker.run_once() for _ in range(4)]
    assert outcomes[0] == {"retry": 1}
    assert {"dead_lettered": 1} in outcomes
    dlq = r.xrange(DLQ_STREAM)
    assert len(dlq) == 1 and b"malformed JSON" in dlq[0][1][b"error"]
    assert r.xpending(FILINGS_STREAM, INGEST_GROUP)["pending"] == 0


def test_subscriber_applies_updates_idempotently_and_invalidates_cache(r, events) -> None:
    publish(r, events[:4])
    IngestWorker(r, fixture_source(), "w", block_ms=1).run_once()
    store = FactStore()
    key_before = cache_key(question="q", context={}, locale="en", mode="mock", version="1", data_version=store.data_version)
    sub = FactSubscriber(r, store, block_ms=1)
    assert sub.poll() == 4
    n = store.count()
    assert n > 0 and sub.poll() == 0
    # Redelivery of the same filings (a new worker replaying them) changes nothing.
    r.delete("edgar:seen_accessions")
    publish(r, events[:4])
    IngestWorker(r, fixture_source(), "w2", block_ms=1).run_once()
    sub.poll()
    assert store.count() == n
    key_after = cache_key(question="q", context={}, locale="en", mode="mock", version="1", data_version=store.data_version)
    assert key_before != key_after


def test_subscriber_skips_malformed_message(r) -> None:
    r.xadd(FACTS_STREAM, {"accession": "x", "ticker": "T", "accepted_at": "", "facts": "not json"})
    sub = FactSubscriber(r, FactStore(), block_ms=1)
    assert sub.poll() == 1 and sub.store.count() == 0


def test_lag_parsing() -> None:
    assert _lag_seconds("2020-01-01T00:00:00.000Z") > 0
    assert _lag_seconds("garbage") is None


class _FlakyRedis:
    """Delegates to fakeredis but raises TimeoutError on the first N calls of
    one method: the failure a too-short socket timeout produced in the live stack."""

    def __init__(self, inner, method: str, failures: int) -> None:
        self._inner, self._method, self.failures = inner, method, failures

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if name != self._method:
            return attr

        def flaky(*a, **kw):
            if self.failures > 0:
                self.failures -= 1
                import redis

                raise redis.TimeoutError("Timeout reading from socket")
            return attr(*a, **kw)

        return flaky


def test_subscriber_thread_survives_timeouts_and_reports_health(r, events) -> None:
    import time

    publish(r, events[:2])
    IngestWorker(r, fixture_source(), "w", block_ms=1).run_once()
    store = FactStore()
    sub = FactSubscriber(_FlakyRedis(r, "xread", failures=1), store, block_ms=1)
    assert sub.health() == "stale"
    sub.start()
    try:
        deadline = time.monotonic() + 10
        while store.count() == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        sub.stop()
    assert store.count() > 0  # the thread retried after the timeout instead of dying
    assert sub.health() == "ok"


def test_worker_loop_survives_redis_timeouts(r, events) -> None:
    publish(r, events[:3])
    sleeps: list[float] = []
    worker = IngestWorker(_FlakyRedis(r, "xautoclaim", failures=2), fixture_source(), "w", block_ms=1)
    worker.run_forever(max_iterations=4, sleep=sleeps.append)
    assert sleeps == [1.0, 2.0]  # exponential backoff, then recovery
    assert r.xlen(FACTS_STREAM) == 3

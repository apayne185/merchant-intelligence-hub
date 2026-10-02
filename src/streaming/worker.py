"""
Ingest worker: consumes edgar:filings in the `ingest` consumer group, turns
each filing into normalized XBRL fact versions, and broadcasts them on
edgar:facts.

Delivery semantics:
  - at-least-once: a message is XACKed only after its facts are published;
  - crash recovery: every loop first XAUTOCLAIMs messages another consumer
    has held longer than `claim_idle_ms`, so a dead worker's in-flight
    filings are finished by the survivors;
  - poison messages: a message delivered more than `max_deliveries` times is
    copied to edgar:filings:dlq with its error and acknowledged, so one bad
    filing cannot block the stream;
  - idempotency: consumers of edgar:facts upsert by (ticker, metric, period,
    accession), so a redelivered filing changes nothing.

Run any number of replicas; scale-out is just more consumers in the group.

    python -m src.streaming.worker --source live      # fetch companyfacts from SEC
    python -m src.streaming.worker --source fixtures  # offline: read data/xbrl/
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import redis
from prometheus_client import Counter, Histogram, start_http_server
from src.filings.edgar import EdgarClient
from src.filings.xbrl import normalize_company_facts
from src.streaming.events import (
    ATTEMPTS_HASH,
    DLQ_STREAM,
    FACTS_STREAM,
    FILINGS_STREAM,
    INGEST_GROUP,
    STREAM_MAXLEN,
    FilingEvent,
    facts_message,
    stream_client,
)

logger = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parents[2]

INGEST_MESSAGES = Counter("ingest_messages_total", "Filing events processed.", ["outcome"])
INGEST_DURATION = Histogram("ingest_processing_seconds", "Fetch + normalize + publish time per filing.",
                            buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30))
INGEST_LAG = Histogram("ingest_filing_lag_seconds", "SEC acceptance time to facts published.",
                       buckets=(1, 5, 15, 30, 60, 120, 300, 900, 3600, 86400, 604800, 1e9))

FactSource = Callable[[FilingEvent], list[dict[str, Any]]]


def live_source(client: EdgarClient) -> FactSource:
    def fetch(ev: FilingEvent) -> list[dict[str, Any]]:  # pragma: no cover - network
        facts = normalize_company_facts(client.company_facts(ev.cik), ev.ticker)
        return [f.to_dict() for f in facts if f.accession == ev.accession]

    return fetch


def fixture_source(xbrl_dir: Path = REPO_ROOT / "data" / "xbrl") -> FactSource:
    def fetch(ev: FilingEvent) -> list[dict[str, Any]]:
        rows = json.loads((xbrl_dir / f"{ev.ticker}.json").read_text())
        return [r for r in rows if r["accession"] == ev.accession]

    return fetch


def _lag_seconds(accepted_at: str) -> float | None:
    try:
        accepted = datetime.fromisoformat(accepted_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if accepted.tzinfo is None:
        accepted = accepted.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - accepted).total_seconds())


class IngestWorker:
    def __init__(
        self, r: redis.Redis, source: FactSource, consumer: str | None = None, *, batch: int = 16,
        block_ms: int = 5_000, claim_idle_ms: int = 60_000, max_deliveries: int = 5,
    ) -> None:
        self.r = r
        self.source = source
        self.consumer = consumer or f"{socket.gethostname()}-{os.getpid()}"
        self.batch = batch
        self.block_ms = block_ms
        self.claim_idle_ms = claim_idle_ms
        self.max_deliveries = max_deliveries
        self.ensure_group()

    def ensure_group(self) -> None:
        try:
            self.r.xgroup_create(FILINGS_STREAM, INGEST_GROUP, id="0", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def _attempt(self, msg_id: str) -> int:
        """Attempt counter kept in a hash rather than read from XPENDING's
        delivery count, which not every Redis-compatible server reports."""
        return int(self.r.hincrby(ATTEMPTS_HASH, msg_id, 1))

    def _done(self, msg_id: str) -> None:
        self.r.xack(FILINGS_STREAM, INGEST_GROUP, msg_id)
        self.r.hdel(ATTEMPTS_HASH, msg_id)

    def handle(self, msg_id: str, fields: dict[Any, Any]) -> str:
        """Processes one message; returns its outcome label."""
        t0 = time.perf_counter()
        attempt = self._attempt(msg_id)
        try:
            ev = FilingEvent.from_fields(fields)
            facts = self.source(ev)
            self.r.xadd(FACTS_STREAM, facts_message(ev, facts), maxlen=STREAM_MAXLEN, approximate=True)  # type: ignore[arg-type]
        except Exception as exc:
            if attempt >= self.max_deliveries:
                payload = {**{_id(k): _id(v) for k, v in fields.items()}, "error": repr(exc)[:500],
                           "source_id": msg_id, "attempts": str(attempt)}
                self.r.xadd(DLQ_STREAM, payload)  # type: ignore[arg-type]
                self._done(msg_id)
                logger.error("filing %s dead-lettered after %d deliveries", msg_id, self.max_deliveries)
                return "dead_lettered"
            logger.warning("filing %s failed, will be retried: %r", msg_id, exc)
            return "retry"
        self._done(msg_id)
        INGEST_DURATION.observe(time.perf_counter() - t0)
        lag = _lag_seconds(ev.accepted_at)
        if lag is not None:
            INGEST_LAG.observe(lag)
        return "ok"

    def run_once(self) -> dict[str, int]:
        outcomes: dict[str, int] = {}

        def tally(outcome: str) -> None:
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
            INGEST_MESSAGES.labels(outcome=outcome).inc()

        # 1. Reclaim messages stuck with dead or slow consumers (and our own retries).
        claim: Any = self.r.xautoclaim(
            FILINGS_STREAM, INGEST_GROUP, self.consumer, min_idle_time=self.claim_idle_ms, start_id="0-0",
            count=self.batch,
        )
        for msg_id, fields in claim[1]:
            tally(self.handle(_id(msg_id), fields))
        # 2. New messages.
        resp: Any = self.r.xreadgroup(INGEST_GROUP, self.consumer, {FILINGS_STREAM: ">"}, count=self.batch,
                                 block=self.block_ms)
        for _stream, messages in resp or []:
            for msg_id, fields in messages:
                tally(self.handle(_id(msg_id), fields))
        return outcomes

    def run_forever(self, max_iterations: int | None = None, sleep: Callable[[float], None] = time.sleep) -> None:
        """Never exits on a Redis error (timeouts included): the container
        restarting would reset metrics and drop in-flight claims for nothing."""
        backoff = 1.0
        n = 0
        while max_iterations is None or n < max_iterations:
            n += 1
            try:
                self.run_once()
                backoff = 1.0
            except redis.RedisError:
                logger.warning("redis error; retrying in %.0fs", backoff, exc_info=True)
                sleep(backoff)
                backoff = min(backoff * 2, 30.0)


def _id(v: Any) -> str:
    return v.decode() if isinstance(v, bytes) else str(v)


def main() -> None:  # pragma: no cover - process entrypoint
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["live", "fixtures"], default="fixtures")
    ap.add_argument("--metrics-port", type=int, default=9102)
    args = ap.parse_args()
    start_http_server(args.metrics_port)
    r = stream_client(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    source = live_source(EdgarClient.from_env()) if args.source == "live" else fixture_source()
    IngestWorker(r, source).run_forever()


if __name__ == "__main__":  # pragma: no cover
    main()

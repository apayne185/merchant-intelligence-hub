"""
Event contracts and stream names for EDGAR ingestion.

    poller --XADD--> edgar:filings --XREADGROUP(ingest)--> workers --XADD--> edgar:facts --XREAD--> API replicas
                                         |                                                      (broadcast)
                            > max deliveries: edgar:filings:dlq

edgar:filings is a work queue: a consumer group gives competing consumers,
at-least-once delivery and a pending list to reclaim from crashed workers.
edgar:facts is a broadcast log: every API replica reads all of it with plain
XREAD (no group), because each replica holds its own copy of the fact store.
Both streams are length-capped; the facts stream's cap bounds how far back a
newly started replica can catch up, beyond which it relies on the fixtures
it boots from.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

import redis

FILINGS_STREAM = "edgar:filings"
FACTS_STREAM = "edgar:facts"
DLQ_STREAM = "edgar:filings:dlq"
SEEN_SET = "edgar:seen_accessions"
ATTEMPTS_HASH = "edgar:filings:attempts"
INGEST_GROUP = "ingest"
STREAM_MAXLEN = 10_000


BLOCK_MS = 5_000


def stream_client(url: str, block_ms: int = BLOCK_MS) -> redis.Redis:
    """Client for blocking stream reads. Its socket timeout must exceed the
    XREAD/XREADGROUP block time: with redis-py's default (5 s, equal to the
    block) or the API's fail-fast cache client (0.25 s), every quiet period
    raises TimeoutError instead of returning an empty read."""
    return redis.Redis.from_url(
        url, socket_timeout=block_ms / 1000 + 5, socket_connect_timeout=5, health_check_interval=30
    )


@dataclass(frozen=True)
class FilingEvent:
    ticker: str
    cik: int
    accession: str
    form: str
    filed: str
    accepted_at: str  # EDGAR acceptanceDateTime, ISO-8601 UTC

    def to_fields(self) -> dict[str, str]:
        return {k: str(v) for k, v in asdict(self).items()}

    @classmethod
    def from_fields(cls, fields: dict[str, Any]) -> FilingEvent:
        f = {_s(k): _s(v) for k, v in fields.items()}
        return cls(ticker=f["ticker"], cik=int(f["cik"]), accession=f["accession"], form=f["form"],
                   filed=f["filed"], accepted_at=f["accepted_at"])


def _s(v: Any) -> str:
    return v.decode() if isinstance(v, bytes) else str(v)


def facts_message(event: FilingEvent, facts: list[dict[str, Any]]) -> dict[str, str]:
    return {"accession": event.accession, "ticker": event.ticker, "accepted_at": event.accepted_at,
            "facts": json.dumps(facts, separators=(",", ":"))}


def parse_facts_message(fields: dict[Any, Any]) -> tuple[str, list[dict[str, Any]]]:
    f = {_s(k): _s(v) for k, v in fields.items()}
    return f["accession"], list(json.loads(f["facts"]))

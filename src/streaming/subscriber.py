"""
Fact-update subscriber: runs inside each API replica, tails edgar:facts and
applies new fact versions to the replica's in-memory FactStore.

Plain XREAD from the last applied id (no consumer group): every replica must
apply every update. On start it reads from the beginning of the capped
stream, so a replica that restarts converges to the same state as its peers.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

import redis
from prometheus_client import Counter, Gauge
from src.filings.factstore import FactStore
from src.filings.xbrl import Fact
from src.streaming.events import FACTS_STREAM, parse_facts_message

logger = logging.getLogger(__name__)

FACT_UPDATES = Counter("copilot_fact_updates_applied_total", "Filings applied to the in-memory fact store.")
FACT_STORE_VERSION = Gauge("copilot_fact_store_version", "Fact store data version (bumps on every applied filing).")
SUBSCRIBER_ERRORS = Counter("copilot_fact_subscriber_errors_total", "Failed reads of edgar:facts (the loop retries).")
STALE_AFTER_S = 60.0


class FactSubscriber:
    def __init__(self, r: redis.Redis, store: FactStore, block_ms: int = 5_000) -> None:
        self.r = r
        self.store = store
        self.block_ms = block_ms
        self.last_id = "0-0"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_ok: float | None = None

    def health(self) -> str:
        """"ok" if the last read succeeded within STALE_AFTER_S, else "stale".
        Reported by /ready, never failing it: a replica that cannot receive
        updates still serves its last consistent snapshot."""
        if self.last_ok is not None and time.monotonic() - self.last_ok < STALE_AFTER_S:
            return "ok"
        return "stale"

    def apply(self, fields: dict[Any, Any]) -> int:
        accession, facts = parse_facts_message(fields)
        n = self.store.upsert(Fact(**{k: v for k, v in f.items() if k != "fact_id"}) for f in facts)
        FACT_UPDATES.inc()
        FACT_STORE_VERSION.set(self.store.data_version)
        logger.info("applied %d fact version(s) from %s", n, accession)
        return n

    def poll(self) -> int:
        """One XREAD round; returns the number of messages applied."""
        resp: Any = self.r.xread({FACTS_STREAM: self.last_id}, count=100, block=self.block_ms)
        self.last_ok = time.monotonic()
        applied = 0
        for _stream, messages in resp or []:
            for msg_id, fields in messages:
                try:
                    self.apply(fields)
                except Exception:
                    logger.exception("malformed facts message %r skipped", msg_id)
                self.last_id = msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id)
                applied += 1
        return applied

    def start(self) -> None:
        def loop() -> None:
            # Any failure is retried with backoff: if this thread died, the
            # replica would silently stop applying filings (FactStoreReplicasDiverged).
            backoff = 1.0
            while not self._stop.is_set():
                try:
                    self.poll()
                    backoff = 1.0
                except Exception:
                    SUBSCRIBER_ERRORS.inc()
                    logger.warning("fact subscriber read failed; retrying in %.0fs", backoff, exc_info=True)
                    self._stop.wait(backoff)
                    backoff = min(backoff * 2, 30.0)

        self._thread = threading.Thread(target=loop, name="fact-subscriber", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

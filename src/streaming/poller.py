"""
EDGAR poller: detects new 10-K/10-Q filings for the covered universe and
publishes one FilingEvent per filing to edgar:filings.

Exactly-once *publication* despite restarts and multiple pollers: an event is
published only if SADD on the seen-accessions set returns 1, which Redis
executes atomically. Downstream processing is still at-least-once (a worker
can crash mid-message), which the idempotent fact upsert absorbs.

    python -m src.streaming.poller --live      # poll data.sec.gov (needs SEC_USER_AGENT)
    python -m src.streaming.poller --replay    # publish data/filings/filing_index.json (offline demo)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import redis
from src.filings.edgar import EdgarClient, recent_filings
from src.streaming.events import FILINGS_STREAM, SEEN_SET, STREAM_MAXLEN, FilingEvent, stream_client

logger = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parents[2]
INDEX_PATH = REPO_ROOT / "data" / "filings" / "filing_index.json"
UNIVERSE_PATH = REPO_ROOT / "data" / "universe.json"


def publish(r: redis.Redis, events: Iterable[FilingEvent]) -> int:
    published = 0
    for ev in events:
        if r.sadd(SEEN_SET, ev.accession) == 1:
            r.xadd(FILINGS_STREAM, ev.to_fields(), maxlen=STREAM_MAXLEN, approximate=True)  # type: ignore[arg-type]
            published += 1
    return published


def events_from_index(rows: list[dict[str, Any]]) -> list[FilingEvent]:
    return [
        FilingEvent(ticker=row["ticker"], cik=int(row["cik"]), accession=row["accessionNumber"], form=row["form"],
                    filed=row["filingDate"], accepted_at=row["acceptanceDateTime"])
        for row in rows
    ]


def poll_once(client: EdgarClient, universe: list[dict[str, Any]]) -> list[FilingEvent]:
    events: list[FilingEvent] = []
    for company in universe:
        rows = recent_filings(client.submissions(company["cik"]))[:4]
        events += events_from_index([{**row, "ticker": company["ticker"], "cik": company["cik"]} for row in rows])
    return events


def main() -> None:  # pragma: no cover - process entrypoint
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--replay", action="store_true")
    ap.add_argument("--interval", type=float, default=60.0, help="seconds between live polls")
    args = ap.parse_args()

    r = stream_client(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    if args.replay:
        n = publish(r, events_from_index(json.loads(INDEX_PATH.read_text())))
        logger.info("replayed %d filing event(s)", n)
        return
    client = EdgarClient.from_env()
    universe = json.loads(UNIVERSE_PATH.read_text())
    while True:
        try:
            n = publish(r, poll_once(client, universe))
            logger.info("poll complete: %d new filing(s)", n)
        except Exception:
            logger.exception("poll failed; retrying next interval")
        time.sleep(args.interval)


if __name__ == "__main__":  # pragma: no cover
    main()

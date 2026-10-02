"""
SEC EDGAR client: companyfacts (XBRL), submissions (filing index) and raw
filing documents.

SEC's fair-access policy is enforced here rather than left to callers:
  - a declared User-Agent with a contact address (SEC_USER_AGENT); requests
    without one are rejected by sec.gov with 403, so this fails fast instead;
  - at most 10 requests/second per process (token bucket, thread-safe, so the
    streaming worker's concurrent fetches share one budget);
  - retries with exponential backoff and jitter on 429/5xx and transport
    errors, never on other 4xx (a 404 is an answer, not an outage).
"""
from __future__ import annotations

import os
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

DATA_BASE = "https://data.sec.gov"
ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class EdgarError(RuntimeError):
    pass


class TokenBucket:
    """Classic token bucket: `rate` tokens/second, burst up to `capacity`."""

    def __init__(self, rate: float, capacity: int, clock: Any = time.monotonic, sleep: Any = time.sleep) -> None:
        self.rate = rate
        self.capacity = capacity
        self._tokens = float(capacity)
        self._last = clock()
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = self._clock()
                self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                wait = (1 - self._tokens) / self.rate
            self._sleep(wait)


def cik10(cik: int | str) -> str:
    """EDGAR's zero-padded 10-digit CIK, as used in data.sec.gov paths."""
    return str(int(cik)).zfill(10)


@dataclass
class EdgarClient:
    user_agent: str
    max_retries: int = 4
    timeout_s: float = 30.0
    # One bucket per client; SEC's limit is 10 requests/second per requester.
    bucket: TokenBucket = field(default_factory=lambda: TokenBucket(rate=10, capacity=10))
    transport: httpx.BaseTransport | None = None

    def __post_init__(self) -> None:
        if "@" not in self.user_agent:
            raise EdgarError("SEC_USER_AGENT must include a contact email, e.g. 'Jane Doe jane@example.com'")
        self._http = httpx.Client(
            headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=self.timeout_s,
            transport=self.transport,
            follow_redirects=True,
        )

    @classmethod
    def from_env(cls) -> EdgarClient:
        ua = os.environ.get("SEC_USER_AGENT", "")
        if not ua:
            raise EdgarError("SEC_USER_AGENT is not set (SEC requires a contact User-Agent)")
        return cls(user_agent=ua)

    def _get(self, url: str) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self.bucket.acquire()
            try:
                resp = self._http.get(url)
            except httpx.TransportError as exc:
                last_exc = exc
            else:
                if resp.status_code < 400:
                    return resp
                if resp.status_code not in _RETRYABLE_STATUS:
                    raise EdgarError(f"GET {url} -> HTTP {resp.status_code}")
                last_exc = EdgarError(f"GET {url} -> HTTP {resp.status_code}")
            if attempt < self.max_retries:
                # Full jitter: spreads retries from concurrent workers apart.
                time.sleep(random.uniform(0, min(8.0, 0.5 * 2**attempt)))  # nosec B311 - jitter, not crypto
        raise EdgarError(f"GET {url} failed after {self.max_retries + 1} attempts") from last_exc

    def company_facts(self, cik: int | str) -> dict[str, Any]:
        payload: dict[str, Any] = self._get(f"{DATA_BASE}/api/xbrl/companyfacts/CIK{cik10(cik)}.json").json()
        return payload

    def submissions(self, cik: int | str) -> dict[str, Any]:
        payload: dict[str, Any] = self._get(f"{DATA_BASE}/submissions/CIK{cik10(cik)}.json").json()
        return payload

    def filing_document(self, cik: int | str, accession: str, document: str) -> str:
        return self._get(f"{ARCHIVES_BASE}/{int(cik)}/{accession.replace('-', '')}/{document}").text

    def close(self) -> None:
        self._http.close()


def recent_filings(submissions: dict[str, Any], forms: tuple[str, ...] = ("10-K", "10-Q")) -> list[dict[str, Any]]:
    """Flattens the column-oriented `filings.recent` block of a submissions
    payload into one dict per filing, newest first, filtered by form type."""
    recent = submissions.get("filings", {}).get("recent", {})
    keys = ("accessionNumber", "form", "filingDate", "reportDate", "acceptanceDateTime", "primaryDocument")
    rows = zip(*(recent.get(k, []) for k in keys), strict=False)
    out = [dict(zip(keys, row, strict=True)) for row in rows]
    return [r for r in out if r["form"] in forms]

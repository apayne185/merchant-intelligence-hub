"""
Request audit log (DECISIONS.md D15): one Postgres row per /ask call: who asked
(JWT subject), when, what the guardrails did, which tools ran, outcome,
latency, and the request/trace ids that join it to logs and traces.

Never stores the question text, only its SHA-256 (of the *redacted*
text), enough to spot repeated/abusive queries without the audit table
becoming a second copy of whatever sensitive text users paste in.

Written off the request path (FastAPI BackgroundTasks) and fails open:
an audit-DB outage is logged and counted, it doesn't fail /ask. That's a
deliberate availability-over-completeness choice for an analytics copilot;
a system where every action *must* be audited (payments, access grants)
would make this write synchronous and fail closed instead.

Schema is created idempotently on startup (CREATE TABLE IF NOT EXISTS):
fine for a single append-only table; anything with evolving schema would
get a real migration tool (alembic) first.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from typing import Protocol

from src.copilot.infra.metrics import GUARDRAIL_EVENTS

logger = logging.getLogger(__name__)

DDL = """
CREATE TABLE IF NOT EXISTS copilot_audit_log (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_id      TEXT NOT NULL,
    trace_id        TEXT,
    subject         TEXT NOT NULL,
    outcome         TEXT NOT NULL,
    status_code     INTEGER NOT NULL,
    mode            TEXT NOT NULL,
    route           TEXT[] NOT NULL DEFAULT '{}',
    latency_ms      INTEGER NOT NULL,
    pii_redactions  JSONB NOT NULL DEFAULT '{}'::jsonb,
    question_sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS copilot_audit_log_created_at_idx ON copilot_audit_log (created_at);
CREATE INDEX IF NOT EXISTS copilot_audit_log_subject_idx ON copilot_audit_log (subject, created_at);
"""

# Arbitrary app-wide constant for pg_advisory_xact_lock around schema init.
_SCHEMA_LOCK_ID = 7_421_001

_INSERT = """
INSERT INTO copilot_audit_log
    (created_at, request_id, trace_id, subject, outcome, status_code, mode, route,
     latency_ms, pii_redactions, question_sha256)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
"""


def question_digest(redacted_question: str) -> str:
    return hashlib.sha256(redacted_question.encode()).hexdigest()


@dataclass(frozen=True)
class AuditRecord:
    request_id: str
    trace_id: str | None
    subject: str
    outcome: str
    status_code: int
    mode: str
    latency_ms: int
    question_sha256: str
    route: list[str] = field(default_factory=list)
    pii_redactions: dict[str, int] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class AuditSink(Protocol):
    def write(self, record: AuditRecord) -> None: ...
    def health(self) -> str: ...
    def close(self) -> None: ...


class NullAuditSink:
    """Default when AUDIT_DATABASE_URL is unset."""

    def write(self, record: AuditRecord) -> None:
        return None

    def health(self) -> str:
        return "disabled"

    def close(self) -> None:
        return None


class PostgresAuditSink:
    def __init__(self, dsn: str, timeout: float = 2.0) -> None:
        from psycopg_pool import ConnectionPool

        # open=False + lazy open: constructing the app (and importing it in
        # tests) must never block on a database connection.
        self._pool = ConnectionPool(dsn, min_size=1, max_size=4, open=False, timeout=timeout)
        self._opened = False
        self._schema_ready = False
        self._init_lock = threading.Lock()

    def _ensure(self) -> None:
        if self._schema_ready:
            return
        # CREATE TABLE IF NOT EXISTS is not atomic in Postgres: two
        # concurrent callers can both pass the existence check and one dies
        # on a pg_class unique violation. Found live in docker-compose, the
        # first two background audit writes raced and one row was lost. The
        # thread lock covers this process; the transaction-scoped advisory
        # lock covers several replicas starting at once (k8s rollout).
        with self._init_lock:
            if self._schema_ready:
                return
            if not self._opened:
                self._pool.open(wait=False)
                self._opened = True
            with self._pool.connection() as conn:
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_LOCK_ID,))
                conn.execute(DDL)
            self._schema_ready = True

    def write(self, record: AuditRecord) -> None:
        try:
            self._ensure()
            with self._pool.connection() as conn:
                conn.execute(
                    _INSERT,
                    (
                        record.created_at,
                        record.request_id,
                        record.trace_id,
                        record.subject,
                        record.outcome,
                        record.status_code,
                        record.mode,
                        record.route,
                        record.latency_ms,
                        json.dumps(record.pii_redactions),
                        record.question_sha256,
                    ),
                )
        except Exception:
            logger.warning("audit write failed; request_id=%s", record.request_id, exc_info=True)
            GUARDRAIL_EVENTS.labels(event="audit_write_error").inc()

    def health(self) -> str:
        try:
            self._ensure()
            with self._pool.connection() as conn:
                conn.execute("SELECT 1")
            return "ok"
        except Exception:
            return "unavailable"

    def close(self) -> None:
        if self._opened:
            self._pool.close()


@lru_cache(maxsize=2)
def _sink_for(dsn: str | None) -> AuditSink:
    return PostgresAuditSink(dsn) if dsn else NullAuditSink()

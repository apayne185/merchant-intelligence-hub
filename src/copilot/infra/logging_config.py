"""
Structured JSON logging with correlation IDs (D55).

Every log line carries `request_id` (from the X-Request-ID header, or
generated) and the active OTel `trace_id`/`span_id` — so a log line in
Loki/CloudWatch/ELK can be joined to its trace in Jaeger and to its row in
the audit table, and one ID pasted into a support ticket finds all three.

LOG_FORMAT=json in containers (Dockerfile/k8s); plain text locally, where
a human reads the terminal.
"""
from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

from opentelemetry import trace

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Attributes every LogRecord has — anything else on the record came from
# `extra={...}` and belongs in the JSON output.
# `color_message` is uvicorn's ANSI-coloured duplicate of `message` — noise in JSON.
_STD_ATTRS = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName", "color_message"}


def _correlation() -> dict[str, str]:
    out: dict[str, str] = {}
    rid = request_id_var.get()
    if rid:
        out["request_id"] = rid
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        out["trace_id"] = format(ctx.trace_id, "032x")
        out["span_id"] = format(ctx.span_id, "016x")
    return out


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **_correlation(),
        }
        for key, value in vars(record).items():
            if key not in _STD_ATTRS and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


class TextFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)s [%(correlation)s] %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        c = _correlation()
        record.correlation = f"rid={c.get('request_id', '-')} trace={c.get('trace_id', '-')[:8]}"
        return super().format(record)


def configure_logging(fmt: str = "text", level: str = "INFO") -> None:
    """Replaces the root handler, and routes uvicorn's own loggers through
    it so the container's stdout is one consistent format. Called from the
    app's lifespan — i.e. after uvicorn has installed its own log config,
    which this deliberately overrides."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
    # The middleware emits its own access line (with request_id/trace_id/
    # duration); uvicorn's would be a duplicate without any of those.
    logging.getLogger("uvicorn.access").disabled = True

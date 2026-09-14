"""
Observability for the orchestrator graph — real OpenTelemetry spans (the
standard tracing API/data model), no collector to stand up. One span per
graph node (route/data_analyst/risk/grounding/complaint_classifier/
synthesize) plus a root span per /ask request, nested via OTel's normal
parent/child context propagation — so a trace shows exactly which
specialist(s) fired, in what order, and how long each took, without
guessing from `tool_calls` timestamps after the fact.

Same "no new infra this repo can't run locally" philosophy as D17-D19's
in-repo vector store and D26's no-checkpointer decision: exporters here
write to the console or a local JSON-lines file, never to a collector
endpoint — MOCK_LLM=1 stays fully offline and deterministic in span
*structure* (span attributes like duration are naturally non-deterministic
wall-clock values, so tests assert on span names/attributes, never on
timing). See DECISIONS.md D37.

Exporter selection via COPILOT_TRACE_EXPORTER env var:
  - unset/"none" (default): tracing is a no-op — spans are created (cheap,
    same code path always runs so it's actually exercised in tests) but
    never exported anywhere. Zero behavior change for existing deployments.
  - "console": human-readable spans printed to stdout as they end.
  - "file": newline-delimited JSON spans appended to
    COPILOT_TRACE_FILE (default: outputs/traces.jsonl).
"""
from __future__ import annotations

import os
import threading
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TRACE_FILE = REPO_ROOT / "outputs" / "traces.jsonl"


class _NoOpExporter(SpanExporter):
    """Discards every span. Used when tracing isn't configured — spans are
    still created (so the instrumented code path is always exercised, in
    tests included) but cost nothing beyond span-object construction."""

    def export(self, spans) -> SpanExportResult:
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass


class JsonLinesFileExporter(SpanExporter):
    """Appends each span as one JSON line to a local file — a durable trace
    log without running a collector. Mirrors this repo's outputs/*.json
    convention (D21/D28/D36 eval reports) but newline-delimited since spans
    arrive incrementally, not as one final report."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def export(self, spans: list[ReadableSpan]) -> SpanExportResult:
        import json

        with self._path.open("a") as f:
            for span in spans:
                f.write(json.dumps(_span_to_dict(span)) + "\n")
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass


def _span_to_dict(span: ReadableSpan) -> dict[str, Any]:
    ctx = span.get_span_context()
    parent = span.parent
    return {
        "name": span.name,
        "trace_id": format(ctx.trace_id, "032x"),
        "span_id": format(ctx.span_id, "016x"),
        "parent_span_id": format(parent.span_id, "016x") if parent else None,
        "start_time_ns": span.start_time,
        "end_time_ns": span.end_time,
        "duration_ms": round((span.end_time - span.start_time) / 1e6, 3) if span.end_time else None,
        "attributes": dict(span.attributes or {}),
        "status": span.status.status_code.name,
    }


def _build_exporter() -> SpanExporter:
    kind = os.environ.get("COPILOT_TRACE_EXPORTER", "none").lower()
    if kind == "console":
        return ConsoleSpanExporter()
    if kind == "file":
        path = Path(os.environ.get("COPILOT_TRACE_FILE", str(DEFAULT_TRACE_FILE)))
        return JsonLinesFileExporter(path)
    return _NoOpExporter()


class _RequestSpanBuffer(SpanExporter):
    """A second, always-on exporter (independent of COPILOT_TRACE_EXPORTER)
    that keeps recently finished spans in a small process-local ring, keyed
    by trace_id, so a caller (api.py's /ask) can pull back just *this
    request's* spans right after graph.invoke() returns — "how long did
    each node take for the request that just ran" answered directly,
    without parsing a log file or standing up a real backend.

    Unlike the SDK's own InMemorySpanExporter (deque + clear-everything),
    this groups by trace_id and pops only the caller's own entry — safe
    under FastAPI's concurrent request handling, where another /ask call
    can have spans in flight at the same time a first one reads its own
    trace back. Bounded by every request eventually popping its own
    trace_id; capped at `max_traces` distinct in-flight traces as a
    backstop against a caller that never reads its trace back.
    """

    def __init__(self, max_traces: int = 256) -> None:
        self._lock = threading.Lock()
        self._by_trace: dict[str, list[ReadableSpan]] = {}
        self._order: deque[str] = deque(maxlen=max_traces)

    def export(self, spans: list[ReadableSpan]) -> SpanExportResult:
        with self._lock:
            for span in spans:
                trace_id = format(span.get_span_context().trace_id, "032x")
                if trace_id not in self._by_trace:
                    if len(self._order) == self._order.maxlen and self._order:
                        self._by_trace.pop(self._order.popleft(), None)
                    self._order.append(trace_id)
                self._by_trace.setdefault(trace_id, []).append(span)
        return SpanExportResult.SUCCESS

    def pop(self, trace_id: str) -> list[ReadableSpan]:
        with self._lock:
            return self._by_trace.pop(trace_id, [])

    def shutdown(self) -> None:
        pass


@lru_cache(maxsize=1)
def _request_span_buffer() -> _RequestSpanBuffer:
    return _RequestSpanBuffer()


@lru_cache(maxsize=1)
def get_tracer() -> trace.Tracer:
    """Process-wide tracer, built once. `lru_cache` here plays the same
    role as api.py's get_graph(): the provider/exporter choice is static
    per-process (driven by env vars read once at startup), so rebuilding it
    per call would be pure repeated setup for a byte-identical tracer.
    """
    provider = TracerProvider(resource=Resource.create({"service.name": "merchant-intelligence-copilot"}))
    provider.add_span_processor(SimpleSpanProcessor(_build_exporter()))
    provider.add_span_processor(SimpleSpanProcessor(_request_span_buffer()))
    return provider.get_tracer("src.copilot")


def get_trace(trace_id: int) -> list[dict[str, Any]]:
    """Pops and returns this trace_id's finished spans as plain dicts
    (name + duration_ms + attributes), sorted by start time. Each trace is
    meant to be read exactly once, by the /ask request it belongs to."""
    spans = _request_span_buffer().pop(format(trace_id, "032x"))
    return sorted((_span_to_dict(s) for s in spans), key=lambda s: s["start_time_ns"])


@contextmanager
def traced(name: str, **attributes: Any) -> Iterator[trace.Span]:
    """Span context manager — records exceptions on the span (status +
    the exception event) and always re-raises, so tracing never changes
    control flow or swallows an error the caller would otherwise see."""
    tracer = get_tracer()
    with tracer.start_as_current_span(name, attributes=attributes) as span:
        try:
            yield span
        except Exception as exc:
            span.set_status(trace.StatusCode.ERROR, str(exc))
            span.record_exception(exc)
            raise


def traced_node(node_name: str, fn: Callable[[dict], dict]) -> Callable[[dict], dict]:
    """Wraps a LangGraph node function in a span named after the node,
    tagging how many pending tools remain and (for the router) nothing
    state-specific — kept generic on purpose so this wrapper works
    identically for every node in graph.py's _NODE_FNS without each node
    needing to know it's being traced (same "tools stay framework-agnostic"
    boundary graph.py's own docstring already draws for LangGraph itself).
    """

    def wrapped(state: dict) -> dict:
        with traced(f"copilot.node.{node_name}") as span:
            result = fn(state)
            tool_calls = result.get("tool_calls")
            if tool_calls is not None:
                span.set_attribute("tool_calls.count", len(tool_calls))
            return result

    wrapped.__name__ = getattr(fn, "__name__", node_name)
    return wrapped

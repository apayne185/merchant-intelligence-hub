"""
Observability for the orchestrator graph: OpenTelemetry spans, one per graph
node (route, fundamentals, filing_search, market_risk, pretrade_check,
synthesize) under a root span per /ask request, so a trace shows which
specialists fired, in what order, and how long each took. Offline by
default; see DECISIONS.md D16.

Exporter selection via COPILOT_TRACE_EXPORTER:
  - unset/"none" (default): spans are created (so the code path is always
    exercised in tests) but never exported.
  - "console": human-readable spans on stdout.
  - "file": newline-delimited JSON spans appended to COPILOT_TRACE_FILE
    (default outputs/traces.jsonl), size-capped with one rotation.
  - "otlp": OTLP/HTTP to an OpenTelemetry Collector (docker-compose.yml,
    k8s/), configured through the standard OTEL_EXPORTER_OTLP_* variables.
    The Collector fans out to Jaeger (traces) and, via its spanmetrics
    connector, to Prometheus.

Tests assert on span names and attributes, never on timing.
"""
from __future__ import annotations

import os
import threading
from collections import deque
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol, TypeVar

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TRACE_FILE = REPO_ROOT / "outputs" / "traces.jsonl"
# Hard cap on the trace file's size, once export() sees the file at or
# past this, it rotates trace.jsonl -> trace.jsonl.1 (overwriting any
# previous .1) rather than growing forever. This is a portfolio/demo repo
# writing to local disk, not a production log pipeline with its own
# rotation/shipping, a size cap here is the minimum needed so
# COPILOT_TRACE_EXPORTER=file can't quietly fill a disk over a long-running
# process. 10MB is generous for a demo (~10k+ requests at a few hundred
# bytes/span, several spans/request) without being a real bound in
# production use, deliberately not configurable via env var, since anyone
# who needs real log rotation should point COPILOT_TRACE_FILE at a path
# already managed by one instead.
_MAX_TRACE_FILE_BYTES = 10 * 1024 * 1024


class _NoOpExporter(SpanExporter):
    """Discards every span. Used when tracing isn't configured, spans are
    still created (so the instrumented code path is always exercised, in
    tests included) but cost nothing beyond span-object construction."""

    def export(self, spans) -> SpanExportResult:
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass


class JsonLinesFileExporter(SpanExporter):
    """Appends each span as one JSON line to a local file, a durable trace
    log without running a collector. Mirrors this repo's outputs/*.json
    convention of the eval reports, but newline-delimited since spans
    arrive incrementally, not as one final report.

    Paired with BatchSpanProcessor (see get_tracer() below), not
    SimpleSpanProcessor: export() batches multiple spans per call instead
    of firing once per span, so this keeps one open file handle across the
    whole batch rather than reopening per span. Also caps the file at
    _MAX_TRACE_FILE_BYTES, rotating to a single ``.1`` backup instead of
    growing without bound, see that constant's comment for why a
    one-generation rotation, not a real log-rotation scheme, is the right
    amount of complexity here.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _rotate_if_needed(self) -> None:
        try:
            size = self._path.stat().st_size
        except FileNotFoundError:
            return
        if size < _MAX_TRACE_FILE_BYTES:
            return
        backup = self._path.with_suffix(self._path.suffix + ".1")
        self._path.replace(backup)

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        import json

        lines = [json.dumps(_span_to_dict(span)) for span in spans]
        with self._lock:
            self._rotate_if_needed()
            with self._path.open("a") as f:
                f.write("\n".join(lines) + "\n")
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass


def _span_to_dict(span: ReadableSpan) -> dict[str, Any]:
    # The SDK types these Optional; a finished SDK span always has both,
    # but the guards keep a malformed span from crashing an exporter.
    ctx = span.get_span_context()
    parent = span.parent
    start, end = span.start_time, span.end_time
    return {
        "name": span.name,
        "trace_id": format(ctx.trace_id, "032x") if ctx else None,
        "span_id": format(ctx.span_id, "016x") if ctx else None,
        "parent_span_id": format(parent.span_id, "016x") if parent else None,
        "start_time_ns": start,
        "end_time_ns": end,
        "duration_ms": round((end - start) / 1e6, 3) if end is not None and start is not None else None,
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
    if kind == "otlp":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        return OTLPSpanExporter()
    return _NoOpExporter()


class _RequestSpanBuffer(SpanExporter):
    """A second, always-on exporter (independent of COPILOT_TRACE_EXPORTER)
    that keeps recently finished spans in a small process-local ring, keyed
    by trace_id, so a caller (api.py's /ask) can pull back just *this
    request's* spans right after graph.invoke() returns, "how long did
    each node take for the request that just ran" answered directly,
    without parsing a log file or standing up a real backend.

    Unlike the SDK's own InMemorySpanExporter (deque + clear-everything),
    this groups by trace_id and pops only the caller's own entry, safe
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

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        with self._lock:
            for span in spans:
                # Skip the HTTP SERVER span the request middleware opens
                # (src/copilot/infra/middleware.py): it ends *after* /ask
                # has already popped its trace, so buffering it would
                # re-create the span-buffer leak this guards against. /ask only ever reads
                # back its own INTERNAL spans (copilot.ask + copilot.node.*).
                if span.kind == trace.SpanKind.SERVER:
                    continue
                ctx = span.get_span_context()
                if ctx is None:
                    continue
                trace_id = format(ctx.trace_id, "032x")
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


_PROVIDERS: list[TracerProvider] = []


@lru_cache(maxsize=1)
def get_tracer() -> trace.Tracer:
    """Process-wide tracer, built once. `lru_cache` here plays the same
    role as api.py's get_graph(): the provider/exporter choice is static
    per-process (driven by env vars read once at startup), so rebuilding it
    per call would be pure repeated setup for a byte-identical tracer.
    """
    # OTEL_SERVICE_NAME / OTEL_RESOURCE_ATTRIBUTES (set in k8s/compose) are
    # merged in by Resource.create(); these are just the defaults.
    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": os.environ.get("OTEL_SERVICE_NAME", "filings-risk-copilot"),
                "deployment.environment": os.environ.get("APP_ENV", "development"),
            }
        )
    )
    # BatchSpanProcessor, not Simple: the console/file exporter's export()
    # used to run synchronously in the request path on every single span
    # end (SimpleSpanProcessor calls export() per span), a blocking
    # stdout write or file open+write+close, ~6 times per /ask, the moment
    # anyone turns tracing on. Batching moves that I/O to a background
    # thread and coalesces multiple spans per export() call. Deliberately
    # NOT applied to the request-span buffer below, api.py's /ask reads
    # that buffer synchronously right after graph.invoke() returns, so it
    # must still see every span immediately, not after a batching delay.
    provider.add_span_processor(BatchSpanProcessor(_build_exporter()))
    provider.add_span_processor(SimpleSpanProcessor(_request_span_buffer()))
    _PROVIDERS.append(provider)
    return provider.get_tracer("src.copilot")


def shutdown_tracing() -> None:
    """Flushes batched spans on graceful shutdown (app lifespan), so the
    last few seconds of spans before a pod's SIGTERM aren't dropped."""
    while _PROVIDERS:
        _PROVIDERS.pop().shutdown()


def get_trace(trace_id: int) -> list[dict[str, Any]]:
    """Pops and returns this trace_id's finished spans as plain dicts
    (name + duration_ms + attributes), sorted by start time. Each trace is
    meant to be read exactly once, by the /ask request it belongs to."""
    spans = _request_span_buffer().pop(format(trace_id, "032x"))
    return sorted((_span_to_dict(s) for s in spans), key=lambda s: s["start_time_ns"])


@contextmanager
def traced(
    name: str,
    *,
    context: Context | None = None,
    kind: trace.SpanKind = trace.SpanKind.INTERNAL,
    **attributes: Any,
) -> Iterator[trace.Span]:
    """Span context manager, records exceptions on the span (status +
    the exception event) and always re-raises, so tracing never changes
    control flow or swallows an error the caller would otherwise see.
    `context` parents the span on an extracted remote context (W3C
    traceparent from the request middleware); default is the current one."""
    tracer = get_tracer()
    with tracer.start_as_current_span(name, context=context, kind=kind, attributes=attributes) as span:
        try:
            yield span
        except Exception as exc:
            span.set_status(trace.StatusCode.ERROR, str(exc))
            span.record_exception(exc)
            raise


_StateT = TypeVar("_StateT", contravariant=True)


class NodeFn(Protocol[_StateT]):
    """A LangGraph node: `(state) -> partial state update`. A Protocol, not
    a Callable alias, because LangGraph's own node protocol matches on the
    parameter *name* `state`, which Callable[[...], ...] erases."""

    def __call__(self, state: _StateT) -> dict[str, Any]: ...


def traced_node(node_name: str, fn: NodeFn[_StateT]) -> NodeFn[_StateT]:
    """Wraps a LangGraph node function in a span named after the node,
    tagging how many pending tools remain and (for the router) nothing
    state-specific, kept generic on purpose so this wrapper works
    identically for every node in graph.py's _NODE_FNS without each node
    needing to know it's being traced (same "tools stay framework-agnostic"
    boundary graph.py's own docstring already draws for LangGraph itself).
    """

    def wrapped(state: _StateT) -> dict[str, Any]:
        with traced(f"copilot.node.{node_name}") as span:
            result = fn(state)
            tool_calls = result.get("tool_calls")
            if tool_calls is not None:
                span.set_attribute("tool_calls.count", len(tool_calls))
            return result

    wrapped.__name__ = getattr(fn, "__name__", node_name)
    return wrapped

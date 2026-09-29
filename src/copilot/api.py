"""
FastAPI app for the Merchant Intelligence Copilot.

Endpoints:
  - GET  /health    liveness (process up + LLM backend configured)
  - GET  /ready     readiness (graph compiled; deps reported, see schemas.py)
  - GET  /metrics   Prometheus exposition (src/copilot/infra/metrics.py)
  - POST /ask       authenticated, rate-limited, guardrailed

Request path for /ask (DECISIONS.md D51-D55):
  RequestContextMiddleware (request id, W3C traceparent, access log)
  -> auth (Bearer JWT, AUTH_MODE)          401/403
  -> rate limit (per subject or IP)        429
  -> guardrails (injection block, PII redaction)  400
  -> response cache (CACHE_TTL_SECONDS)
  -> LangGraph orchestrator
  -> metrics + audit row (background)

Runs independently of src/parte4_api/main.py — the complaint-classifier
service keeps running standalone on its own port; this is the new flagship
entry point, not a replacement mounted into the same app (see DECISIONS.md
D27 for why not).

Starts with:
    export MOCK_LLM=1                 # or export OPENAI_API_KEY=...
    uvicorn src.copilot.api:app --reload --port 8001
"""
from __future__ import annotations

import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Annotated, Any, Literal

from fastapi import BackgroundTasks, Depends, FastAPI
from fastapi.responses import JSONResponse
from src.copilot.graph import build_graph
from src.copilot.infra import store
from src.copilot.infra.audit import AuditRecord, AuditSink, _sink_for, question_digest
from src.copilot.infra.auth import Principal
from src.copilot.infra.cache import ResponseCache, cache_key, get_cache
from src.copilot.infra.guardrails import check_input
from src.copilot.infra.logging_config import configure_logging, request_id_var
from src.copilot.infra.metrics import (
    ASK_REQUESTS,
    CACHE_EVENTS,
    GUARDRAIL_EVENTS,
    NODE_DURATION,
    TOOL_INVOCATIONS,
    instrument_app,
)
from src.copilot.infra.middleware import RequestContextMiddleware
from src.copilot.infra.ratelimit import enforce_rate_limit
from src.copilot.infra.settings import Settings, get_settings
from src.copilot.schemas import AskRequest, AskResponse, NodeTiming, ReadinessResponse
from src.copilot.state import initial_state
from src.copilot.tracing import get_trace, shutdown_tracing, traced
from src.parte4_api.agent import is_mock_mode

# Reused as-is (D22/D25's "share, don't duplicate" reasoning): the health
# contract (status/model/version) is identical to src/parte4_api's.
from src.parte4_api.schemas import HealthResponse

logger = logging.getLogger(__name__)

APP_VERSION = "0.3.0"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # get_settings() validates — a misconfigured deployment (AUTH_MODE=jwt
    # without a key, AUTH_MODE=none under APP_ENV=production) crashes here,
    # at rollout, where the readiness gate catches it before any traffic.
    settings = get_settings()
    configure_logging(settings.log_format, settings.log_level)
    logger.info(
        "copilot starting",
        extra={
            "environment": settings.environment,
            "auth_mode": settings.auth_mode,
            "redis": bool(settings.redis_url),
            "audit_db": bool(settings.audit_database_url),
        },
    )
    yield
    get_audit_sink(settings).close()
    shutdown_tracing()


app = FastAPI(
    title="Merchant Intelligence Copilot",
    version=APP_VERSION,
    description=(
        "Multi-agent orchestrator answering merchant questions via KPI/SQL "
        "tools, a churn-risk model, and policy RAG, with cited answers."
    ),
    lifespan=lifespan,
)
app.add_middleware(RequestContextMiddleware)
instrument_app(app)


@lru_cache(maxsize=1)
def get_graph():
    """Factory for the compiled graph — cached: the graph's structure
    (nodes/edges) is 100% static, so recompiling it fresh on every request
    was pure repeated LangGraph build/compile/validation work for a
    byte-identical result each time. `lru_cache` doesn't interfere with
    tests overriding this via `app.dependency_overrides`, same pattern as
    src/parte4_api/main.py's get_agent()/AgentDep — overriding replaces the
    callable entirely, regardless of whether the default is cached.
    """
    return build_graph()


def get_audit_sink(settings: Annotated[Settings, Depends(get_settings)]) -> AuditSink:
    return _sink_for(settings.audit_database_url)


def get_response_cache(settings: Annotated[Settings, Depends(get_settings)]) -> ResponseCache | None:
    return get_cache(settings.redis_url) if settings.cache_ttl_seconds > 0 else None


GraphDep = Annotated[Any, Depends(get_graph)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
AuditDep = Annotated[AuditSink, Depends(get_audit_sink)]
CacheDep = Annotated["ResponseCache | None", Depends(get_response_cache)]
# One dependency for both auth and rate limiting — enforce_rate_limit runs
# get_principal first, so a 401 never consumes quota.
PrincipalDep = Annotated[Principal, Depends(enforce_rate_limit)]


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """`status="degraded"` if no MOCK_LLM nor OPENAI_API_KEY is configured —
    every /ask request that needs the real router/synthesizer would fail,
    even though the process is alive. Mirrors src/parte4_api/main.py:health().
    """
    if is_mock_mode():
        return HealthResponse(status="ok", model="mock", version=app.version)
    if os.environ.get("OPENAI_API_KEY"):
        return HealthResponse(status="ok", model="gpt-4o-mini", version=app.version)
    return HealthResponse(status="degraded", model="unconfigured", version=app.version)


@app.get("/ready", response_model=ReadinessResponse)
def ready(settings: SettingsDep, audit: AuditDep) -> JSONResponse:
    checks = {"redis": store.ping(settings.redis_url), "audit_db": audit.health()}
    try:
        get_graph()
        checks["graph"] = "ok"
    except Exception:
        logger.exception("readiness: graph failed to build")
        checks["graph"] = "error"
    ok = checks["graph"] == "ok"
    body = ReadinessResponse(status="ready" if ok else "not_ready", checks=checks)
    return JSONResponse(body.model_dump(), status_code=200 if ok else 503)


@app.post("/ask", response_model=AskResponse)
def ask(
    req: AskRequest,
    graph: GraphDep,
    principal: PrincipalDep,
    background: BackgroundTasks,
    audit: AuditDep,
    cache: CacheDep,
    settings: SettingsDep,
) -> AskResponse | JSONResponse:
    """Answers a natural-language merchant question by routing it through
    the orchestrator graph. The whole request runs inside a root
    "copilot.ask" span (src/copilot/tracing.py) so every node span
    graph.invoke() produces nests under it, sharing one trace_id — that
    trace_id is what `trace` in the response is filtered by.
    """
    t0 = time.perf_counter()
    mock = is_mock_mode()
    mode: Literal["mock", "real"] = "mock" if mock else "real"

    decision = check_input(req.question)
    redaction = decision.redaction
    question = redaction.text
    for label, n in redaction.counts.items():
        GUARDRAIL_EVENTS.labels(event=f"pii_{label}").inc(n)

    def _audit(outcome: str, status_code: int, route: list[str], trace_id: str | None) -> None:
        background.add_task(
            audit.write,
            AuditRecord(
                request_id=request_id_var.get() or "",
                trace_id=trace_id,
                subject=principal.subject,
                outcome=outcome,
                status_code=status_code,
                mode=mode,
                latency_ms=int((time.perf_counter() - t0) * 1000),
                question_sha256=question_digest(question),
                route=route,
                pii_redactions=redaction.counts,
            ),
        )

    if decision.blocked:
        GUARDRAIL_EVENTS.labels(event="prompt_injection").inc()
        ASK_REQUESTS.labels(outcome="blocked", mode=mode).inc()
        logger.warning("prompt injection blocked", extra={"subject": principal.subject})
        _audit("blocked", 400, [], None)
        # 400 with a machine-readable code, not a 200 "I can't help with
        # that": the caller's client needs to know the request was refused.
        # Returned, not raised: FastAPI discards BackgroundTasks when the
        # endpoint raises, which would silently drop exactly the audit rows
        # (blocked/error) an auditor most wants to see.
        return JSONResponse({"detail": decision.reason}, status_code=400, background=background)

    key = None
    if cache is not None:
        key = cache_key(
            question=question, merchant_id=req.merchant_id, locale=req.locale, mode=mode, version=app.version
        )
        hit = cache.get(key)
        CACHE_EVENTS.labels(result="hit" if hit else "miss").inc()
        if hit:
            ASK_REQUESTS.labels(outcome="cache_hit", mode=mode).inc()
            resp = AskResponse(**{**hit, "latency_ms": int((time.perf_counter() - t0) * 1000), "trace": [], "cached": True})
            _audit("cache_hit", 200, list(resp.route), None)
            return resp

    state = initial_state(question, merchant_id=req.merchant_id, locale=req.locale, mock=mock)
    trace_id: int | None = None
    node_spans: list[dict] = []
    failed = False
    try:
        with traced("copilot.ask", mock=mock, subject=principal.subject) as root_span:
            trace_id = root_span.get_span_context().trace_id
            try:
                result = graph.invoke(state)
            except Exception:
                # No exponer str(exc) al cliente — same reasoning as /classify:
                # could leak request URLs, model config, or SDK stack traces.
                # `question` is the redacted text, so this log line is PII-free.
                logger.exception("copilot /ask failed for question=%r", question)
                ASK_REQUESTS.labels(outcome="error", mode=mode).inc()
                _audit("error", 502, [], format(trace_id, "032x"))
                failed = True
    finally:
        # get_trace() pops this request's spans out of the shared
        # process-wide buffer (src/copilot/tracing.py's _RequestSpanBuffer)
        # regardless of whether graph.invoke() raised — without this
        # `finally`, an HTTPException propagating out of the `with` block
        # above would skip the pop entirely, leaking that request's spans
        # into the buffer forever (bounded only by its max_traces backstop,
        # never actually reclaimed). Every failed /ask used to leak exactly
        # one trace; confirmed by re-running failing requests and
        # inspecting the buffer's size before this fix. Called exactly
        # once per request (not again below) — get_trace() pops, so a
        # second call on the same trace_id would always return [].
        if trace_id is not None:
            node_spans = get_trace(trace_id)
    if failed:
        return JSONResponse({"detail": "copilot_error"}, status_code=502, background=background)

    trace_summary = [
        NodeTiming(node=s["name"].removeprefix("copilot.node."), duration_ms=s["duration_ms"])
        for s in node_spans
        if s["name"] != "copilot.ask" and s["duration_ms"] is not None
    ]
    for t in trace_summary:
        NODE_DURATION.labels(node=t.node).observe(t.duration_ms / 1000)

    # Distinct tools that actually fired, in first-occurrence order — not
    # the raw tool_calls list, which can have repeats (data_analyst logs
    # one entry per underlying SQL query it ran).
    route = list(dict.fromkeys(tc["tool"] for tc in result["tool_calls"]))
    for tool in route:
        TOOL_INVOCATIONS.labels(tool=tool).inc()

    response = AskResponse(
        question=question,
        route=route,
        answer=result["answer"] or "No information was found for this question.",
        citations=result["citations"],
        tool_calls=result["tool_calls"],
        mode=mode,
        latency_ms=int((time.perf_counter() - t0) * 1000),
        trace=trace_summary,
        pii_redactions=redaction.counts,
    )
    ASK_REQUESTS.labels(outcome="ok", mode=mode).inc()
    _audit("ok", 200, route, format(trace_id, "032x") if trace_id is not None else None)
    if cache is not None and key is not None:
        cache.set(key, response.model_dump(mode="json"), settings.cache_ttl_seconds)
    return response


# -----------------------------------------------------------------------------
# Sanity smoke (manual): `python -m src.copilot.api`
# -----------------------------------------------------------------------------
if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)  # nosec B104 — container entrypoint binds all interfaces by design (D33)

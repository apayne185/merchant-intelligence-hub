"""
FastAPI service for the Filings & Risk Copilot.

Endpoints:
  GET  /health                     liveness
  GET  /ready                      readiness (graph built, fact store loaded; deps reported)
  GET  /metrics                    Prometheus exposition
  POST /ask                        natural-language question -> verified, cited answer
  GET  /v1/facts/{ticker}          point-in-time XBRL facts with provenance (no LLM)
  GET  /v1/facts/{ticker}/{metric}/versions   every filed version of one period (restatement audit)
  POST /v1/risk                    portfolio VaR/ES/backtest via the C++ engine (no LLM)

The /v1 endpoints are the deterministic path for systems that want the
numbers without a language model in the loop; /ask is the same data behind
an agent. Every authenticated endpoint shares the same auth + rate limit.

Request path for /ask:
  RequestContextMiddleware (request id, traceparent, access log)
  -> auth (Bearer JWT)              401/403
  -> rate limit (subject or IP)     429
  -> guardrails (injection, PII)    400
  -> response cache (keyed on data version)
  -> LangGraph orchestrator -> numeric verification
  -> metrics + audit row (background)
"""
from __future__ import annotations

import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Annotated, Any, Literal

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from src.copilot.graph import build_graph, warm_up
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
from src.copilot.mode import is_mock_mode
from src.copilot.schemas import (
    AskRequest,
    AskResponse,
    HealthResponse,
    NodeTiming,
    Position,
    ReadinessResponse,
    VerificationReport,
)
from src.copilot.state import initial_state
from src.copilot.tools.fundamentals import derived_evidence, fact_evidence
from src.copilot.tracing import get_trace, shutdown_tracing, traced
from src.filings.factstore import DERIVED_LABELS, FactStore, get_fact_store
from src.filings.xbrl import METRICS
from src.risk.engine import RiskRequest, portfolio_risk

logger = logging.getLogger(__name__)
APP_VERSION = "1.0.0"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # get_settings() validates: a misconfigured deployment crashes at rollout,
    # where the readiness gate catches it before traffic.
    settings = get_settings()
    configure_logging(settings.log_format, settings.log_level)
    warm_up()
    subscriber = None
    if settings.redis_url and os.environ.get("FACT_STREAM_SUBSCRIBE", "1") == "1":
        from src.streaming.events import stream_client
        from src.streaming.subscriber import FactSubscriber

        subscriber = FactSubscriber(stream_client(settings.redis_url), get_fact_store())
        subscriber.start()
    app.state.fact_subscriber = subscriber
    logger.info("copilot starting", extra={"environment": settings.environment, "auth_mode": settings.auth_mode,
                                           "redis": bool(settings.redis_url), "facts": get_fact_store().count()})
    yield
    if subscriber is not None:
        subscriber.stop()
    get_audit_sink(settings).close()
    shutdown_tracing()


app = FastAPI(
    title="Filings & Risk Copilot",
    version=APP_VERSION,
    description="Multi-agent copilot over SEC filings and market risk with number-level answer verification.",
    lifespan=lifespan,
)
app.add_middleware(RequestContextMiddleware)
instrument_app(app)


@lru_cache(maxsize=1)
def get_graph() -> Any:
    return build_graph()


def get_audit_sink(settings: Annotated[Settings, Depends(get_settings)]) -> AuditSink:
    return _sink_for(settings.audit_database_url)


def get_response_cache(settings: Annotated[Settings, Depends(get_settings)]) -> ResponseCache | None:
    return get_cache(settings.redis_url) if settings.cache_ttl_seconds > 0 else None


GraphDep = Annotated[Any, Depends(get_graph)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
AuditDep = Annotated[AuditSink, Depends(get_audit_sink)]
CacheDep = Annotated["ResponseCache | None", Depends(get_response_cache)]
FactStoreDep = Annotated[FactStore, Depends(get_fact_store)]
# Auth runs inside the rate limiter's dependency, so a 401 never consumes quota.
PrincipalDep = Annotated[Principal, Depends(enforce_rate_limit)]


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    if is_mock_mode():
        return HealthResponse(status="ok", model="mock", version=APP_VERSION)
    if os.environ.get("OPENAI_API_KEY") or os.environ.get("AZURE_OPENAI_ENDPOINT"):
        return HealthResponse(status="ok", model="gpt-4o-mini", version=APP_VERSION)
    return HealthResponse(status="degraded", model="unconfigured", version=APP_VERSION)


@app.get("/ready", response_model=ReadinessResponse)
def ready(settings: SettingsDep, audit: AuditDep, facts: FactStoreDep) -> JSONResponse:
    checks = {"redis": store.ping(settings.redis_url), "audit_db": audit.health()}
    try:
        get_graph()
        checks["graph"] = "ok"
    except Exception:
        logger.exception("readiness: graph failed to build")
        checks["graph"] = "error"
    n = facts.count()
    checks["fact_store"] = "ok" if n > 0 else "empty"
    subscriber = getattr(app.state, "fact_subscriber", None)
    checks["fact_stream"] = subscriber.health() if subscriber is not None else "disabled"
    ok = checks["graph"] == "ok" and n > 0
    body = ReadinessResponse(status="ready" if ok else "not_ready", checks=checks, facts_loaded=n)
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
    facts: FactStoreDep,
) -> AskResponse | JSONResponse:
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
                request_id=request_id_var.get() or "", trace_id=trace_id, subject=principal.subject,
                outcome=outcome, status_code=status_code, mode=mode,
                latency_ms=int((time.perf_counter() - t0) * 1000), question_sha256=question_digest(question),
                route=route, pii_redactions=redaction.counts,
            ),
        )

    if decision.blocked:
        GUARDRAIL_EVENTS.labels(event="prompt_injection").inc()
        ASK_REQUESTS.labels(outcome="blocked", mode=mode).inc()
        logger.warning("prompt injection blocked", extra={"subject": principal.subject})
        _audit("blocked", 400, [], None)
        # Returned, not raised: FastAPI drops BackgroundTasks when the endpoint
        # raises, which would lose exactly the audit rows auditors care about.
        return JSONResponse({"detail": decision.reason}, status_code=400, background=background)

    positions = {p.ticker: p.weight for p in req.portfolio} if req.portfolio else {}
    context = {"tickers": sorted(req.tickers or []), "as_of": req.as_of, "portfolio": positions}
    key = None
    if cache is not None:
        key = cache_key(question=question, context=context, locale=req.locale, mode=mode, version=APP_VERSION,
                        data_version=facts.data_version)
        hit = cache.get(key)
        CACHE_EVENTS.labels(result="hit" if hit else "miss").inc()
        if hit:
            ASK_REQUESTS.labels(outcome="cache_hit", mode=mode).inc()
            resp = AskResponse(**{**hit, "latency_ms": int((time.perf_counter() - t0) * 1000), "trace": [],
                                  "cached": True})
            _audit("cache_hit", 200, list(resp.route), None)
            return resp

    state = initial_state(question, tickers=[t.upper() for t in req.tickers or []], as_of=req.as_of,
                          positions=positions, locale=req.locale, mock=mock)
    trace_id: int | None = None
    node_spans: list[dict[str, Any]] = []
    failed = False
    result: dict[str, Any] = {}
    try:
        with traced("copilot.ask", mock=mock, subject=principal.subject) as root_span:
            trace_id = root_span.get_span_context().trace_id
            try:
                result = graph.invoke(state)
            except Exception:
                # Never return str(exc): it can leak URLs, model config or stack details.
                logger.exception("/ask failed for question=%r", question)
                ASK_REQUESTS.labels(outcome="error", mode=mode).inc()
                _audit("error", 502, [], format(trace_id, "032x"))
                failed = True
    finally:
        # Always pop this request's spans from the shared buffer, or failed
        # requests would leak their spans into it.
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
    route = list(dict.fromkeys(tc["tool"] for tc in result["tool_calls"]))
    for tool in route:
        TOOL_INVOCATIONS.labels(tool=tool).inc()

    response = AskResponse(
        question=question,
        route=route,
        answer=result["answer"] or "No information was found for this question.",
        evidence=result["evidence"],
        verification=VerificationReport(**result["verification"]),
        tool_calls=result["tool_calls"],
        mode=mode,
        as_of=req.as_of,
        latency_ms=int((time.perf_counter() - t0) * 1000),
        trace=trace_summary,
        pii_redactions=redaction.counts,
    )
    ASK_REQUESTS.labels(outcome="ok", mode=mode).inc()
    _audit("ok", 200, route, format(trace_id, "032x") if trace_id is not None else None)
    if cache is not None and key is not None:
        cache.set(key, response.model_dump(mode="json"), settings.cache_ttl_seconds)
    return response


# ----------------------------------------------------------- deterministic /v1
@app.get("/v1/facts/{ticker}")
def get_facts(
    ticker: str,
    principal: PrincipalDep,
    facts: FactStoreDep,
    fiscal_year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
    as_of: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")] = None,
) -> dict[str, Any]:
    ticker = ticker.upper()
    fy = fiscal_year or facts.latest_fiscal_year(ticker, as_of)
    if fy is None:
        raise HTTPException(404, f"no filings for {ticker}")
    reported = [fact_evidence(f, facts) for m in METRICS if (f := facts.get(ticker, m, fy, "FY", as_of))]
    derived = [derived_evidence(d) for name in DERIVED_LABELS if (d := facts.derived(ticker, name, fy, as_of))]
    return {"ticker": ticker, "fiscal_year": fy, "as_of": as_of,
            "reported": [e.model_dump() for e in reported], "derived": [e.model_dump() for e in derived]}


@app.get("/v1/facts/{ticker}/{metric}/versions")
def get_versions(
    ticker: str, metric: str, principal: PrincipalDep, facts: FactStoreDep,
    fiscal_year: Annotated[int, Query(ge=2000, le=2100)], fiscal_period: str = "FY",
) -> dict[str, Any]:
    if metric not in METRICS:
        raise HTTPException(404, f"unknown metric {metric!r}")
    versions = facts.versions(ticker.upper(), metric, fiscal_year, fiscal_period)
    return {
        "ticker": ticker.upper(), "metric": metric, "fiscal_year": fiscal_year, "fiscal_period": fiscal_period,
        "restated": len({v.value for v in versions}) > 1, "versions": [v.to_dict() for v in versions],
    }


class RiskBody(BaseModel):
    portfolio: list[Position] = Field(..., min_length=1, max_length=20)
    as_of: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    confidence: float = Field(default=0.99, gt=0.5, lt=1.0)
    window: int = Field(default=500, ge=30, le=2000)
    horizon_days: int = Field(default=10, ge=1, le=60)
    mc_paths: int = Field(default=100_000, ge=1_000, le=2_000_000)


@app.post("/v1/risk")
def post_risk(body: RiskBody, principal: PrincipalDep) -> dict[str, Any]:
    try:
        return portfolio_risk(RiskRequest(
            tickers=tuple(p.ticker for p in body.portfolio), weights=tuple(p.weight for p in body.portfolio),
            as_of=body.as_of, window=body.window, confidence=body.confidence, horizon_days=body.horizon_days,
            mc_paths=body.mc_paths,
        ))
    except (KeyError, ValueError) as exc:
        raise HTTPException(422, str(exc).strip("'\"")) from exc


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)  # nosec B104 - container entrypoint binds all interfaces by design

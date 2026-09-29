"""
Prometheus metrics for the copilot API (D51).

Two layers:
  - HTTP RED metrics (rate/errors/duration per handler+status) from
    prometheus-fastapi-instrumentator — the generic gateway view.
  - Copilot-specific series below — what the HTTP layer can't see: per-node
    latency inside the graph, which tools fired, LLM token spend and its
    estimated cost, guardrail/auth/rate-limit events, cache hit ratio.

p95/p99 are computed in PromQL (`histogram_quantile`) from the histogram
buckets — see deploy/prometheus/rules.yml and the Grafana dashboard — not
precomputed here: client-side quantiles (Summary) can't be aggregated
across replicas, histograms can.

One uvicorn worker per container (Dockerfile CMD), scaled by replicas: the
default in-process registry is correct as-is, no prometheus_client
multiprocess mode needed.
"""
from __future__ import annotations

import logging
from typing import Any

from prometheus_client import Counter, Histogram

logger = logging.getLogger(__name__)

# Node latencies span ~0.1ms (mock router) to tens of seconds (a slow real
# LLM call) — buckets cover that whole range, denser where the SLO sits.
_NODE_BUCKETS = (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)
HTTP_LATENCY_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)

NODE_DURATION = Histogram(
    "copilot_node_duration_seconds",
    "Wall-clock duration of each LangGraph node per /ask request.",
    ["node"],
    buckets=_NODE_BUCKETS,
)
ASK_REQUESTS = Counter(
    "copilot_ask_requests_total",
    "/ask outcomes: ok | cache_hit | blocked | error.",
    ["outcome", "mode"],
)
TOOL_INVOCATIONS = Counter(
    "copilot_tool_invocations_total",
    "Specialist tools routed to, per /ask request.",
    ["tool"],
)
LLM_TOKENS = Counter(
    "copilot_llm_tokens_total",
    "LLM tokens consumed (real mode only).",
    ["model", "call", "direction"],
)
LLM_COST_USD = Counter(
    "copilot_llm_cost_usd_total",
    "Estimated LLM spend in USD (list price x tokens; an estimate, not a bill).",
    ["model", "call"],
)
GUARDRAIL_EVENTS = Counter(
    "copilot_guardrail_events_total",
    "Guardrail/auth/rate-limit events, e.g. pii_card, prompt_injection, rate_limited.",
    ["event"],
)
CACHE_EVENTS = Counter(
    "copilot_cache_requests_total",
    "/ask response cache lookups: hit | miss | error.",
    ["result"],
)

# USD per 1M tokens (input, output), OpenAI list prices. Used only when the
# provider response doesn't carry its own cost figure.
MODEL_PRICING_PER_1M: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
}


def record_llm_usage(call: str, model: str, run_output: Any) -> None:
    """Records token usage from an Agno RunOutput (`.metrics.input_tokens`/
    `.output_tokens`). Defensive on shape: observability must never break a
    request, so a missing/renamed field logs and returns."""
    try:
        m = getattr(run_output, "metrics", None)
        if m is None:
            return
        tin = int(getattr(m, "input_tokens", 0) or 0)
        tout = int(getattr(m, "output_tokens", 0) or 0)
        LLM_TOKENS.labels(model=model, call=call, direction="input").inc(tin)
        LLM_TOKENS.labels(model=model, call=call, direction="output").inc(tout)
        cost = getattr(m, "cost", None)
        if cost is None and model in MODEL_PRICING_PER_1M:
            pin, pout = MODEL_PRICING_PER_1M[model]
            cost = (tin * pin + tout * pout) / 1_000_000
        if cost:
            LLM_COST_USD.labels(model=model, call=call).inc(float(cost))
    except Exception:
        logger.warning("could not record LLM usage for call=%s", call, exc_info=True)


def instrument_app(app: Any) -> None:
    from prometheus_fastapi_instrumentator import Instrumentator

    Instrumentator(
        should_group_status_codes=False,  # exact codes: a 429 spike != a 401 spike
        excluded_handlers=["/metrics"],
    ).instrument(app, latency_lowr_buckets=HTTP_LATENCY_BUCKETS).expose(app, include_in_schema=False)

"""
Pydantic contracts for the copilot API.

Closed Literal sets for tool names and evidence kinds: the router (LLM or
rules) can only select tools that exist, and every citation in an answer
points at an evidence item the tools actually produced.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

ToolName = Literal["fundamentals", "filing_search", "market_risk", "pretrade_check"]
EvidenceKind = Literal["xbrl_fact", "derived_metric", "risk_metric", "filing_passage", "limit_check"]


class Evidence(BaseModel):
    """One unit of ground truth a tool produced. Numeric `values` are what
    the answer verifier matches every number in the answer against."""

    id: str
    kind: EvidenceKind
    label: str
    ticker: str | None = None
    values: dict[str, float] = Field(default_factory=dict)
    unit: str | None = None
    # Provenance: SEC accession/filing date for filings data, formula + input
    # fact ids for derived metrics, model parameters for risk metrics.
    source: dict[str, Any] = Field(default_factory=dict)
    excerpt: str | None = Field(default=None, max_length=600)


class ToolCallRecord(BaseModel):
    tool: ToolName
    args: dict[str, Any] = Field(default_factory=dict)
    summary: str = Field(..., max_length=300)
    duration_ms: float | None = None


class NodeTiming(BaseModel):
    node: str
    duration_ms: float


class UnverifiedClaim(BaseModel):
    text: str
    value: float


class VerificationReport(BaseModel):
    """Result of checking every number in the answer against the evidence."""

    status: Literal["verified", "failed", "no_numeric_claims"]
    numbers_checked: int
    numbers_verified: int
    unverified: list[UnverifiedClaim] = Field(default_factory=list)
    # True when the LLM's draft failed verification twice and the answer
    # was replaced by the deterministic template (which is correct by construction).
    fallback_used: bool = False
    attempts: int = 1


class Position(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=10)
    weight: float = Field(..., ge=-1.0, le=1.0)

    @field_validator("ticker")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()


class RouteDecision(BaseModel):
    """Structured output of the real-mode router: tool selection and entity
    extraction only. It never sees tools and cannot call anything."""

    tools: list[ToolName]
    tickers: list[str] = Field(default_factory=list)
    fiscal_year: int | None = None
    reasoning: str = Field(..., max_length=300)


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=4_000)
    tickers: list[str] | None = Field(default=None, max_length=20)
    # Point in time: the copilot only uses filings filed and prices dated on
    # or before this date. Omitted = latest available.
    as_of: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    # Proposed portfolio for market_risk / pretrade_check.
    portfolio: list[Position] | None = Field(default=None, max_length=20)
    locale: Literal["en", "es"] = "en"


class AskResponse(BaseModel):
    question: str
    route: list[ToolName]
    answer: str = Field(..., max_length=2500)
    evidence: list[Evidence]
    verification: VerificationReport
    tool_calls: list[ToolCallRecord]
    mode: Literal["mock", "real"]
    as_of: str | None = None
    latency_ms: int
    trace: list[NodeTiming] = Field(default_factory=list)
    pii_redactions: dict[str, int] = Field(default_factory=dict)
    cached: bool = False


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    model: str
    version: str


class ReadinessResponse(BaseModel):
    """/ready: whether this replica should receive traffic. Redis and the
    audit DB are reported but never fail readiness: both degrade gracefully,
    so pulling every pod on a Redis blip would turn a degraded dependency into
    an outage."""

    status: Literal["ready", "not_ready"]
    checks: dict[str, str]
    facts_loaded: int = 0

"""
Pydantic contracts for the Merchant Intelligence Copilot.

These are the copilot's equivalent of src/parte4_api/schemas.py: the
contract for /ask. Closed Literal sets for tool names and citation source
types, same reasoning as schemas.py:Category (D9) — prevents the router/LLM
from inventing values the rest of the system doesn't recognize.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ToolName = Literal["data_analyst", "risk", "grounding", "complaint_classifier"]


class Citation(BaseModel):
    """A grounding source backing a claim in the final answer."""

    source_type: Literal["policy_doc", "historical_case", "kpi_query", "model_output"]
    id: str
    title: str | None = None
    excerpt: str = Field(..., max_length=400)


class ToolCallRecord(BaseModel):
    """One specialist tool invocation, for the caller to audit what actually ran."""

    tool: ToolName
    args: dict[str, Any] = Field(default_factory=dict)
    summary: str = Field(..., max_length=300)


class NodeTiming(BaseModel):
    """One graph-node span's timing, from src/copilot/tracing.py — lets a
    caller see which node was the bottleneck for this specific request,
    not just the total latency_ms."""

    node: str
    duration_ms: float


class RouteDecision(BaseModel):
    """Structured output for the real-mode router's Agno agent — no tools
    attached, classification + argument extraction only. Mirrors agent.py's
    _LLMClassification pattern (D9): the LLM only fills in what it can't
    know from the request itself.
    """

    tools: list[ToolName]
    merchant_id: int | None = None
    reasoning: str = Field(..., max_length=300)


class AskRequest(BaseModel):
    """A natural-language question to the copilot."""

    # max_length=10_000 — every other string field in this module and
    # AskResponse below is capped (Citation.excerpt 400, ToolCallRecord.summary
    # 300, RouteDecision.reasoning 300, AskResponse.answer 1500); this was
    # the one field crossing the actual untrusted-input boundary (a real
    # HTTP request body) left uncapped. 10,000 chars is generous for any
    # realistic question/complaint while still rejecting a multi-MB
    # payload that would otherwise flow uncapped into embeddings
    # (grounding.retrieve_policy) and, in real mode, straight into the
    # router/synthesis LLM prompts.
    question: str = Field(..., min_length=1, max_length=10_000)
    merchant_id: int | None = None
    locale: Literal["es", "pt", "en"] = "en"


class AskResponse(BaseModel):
    """The copilot's structured, cited answer."""

    question: str
    route: list[ToolName]
    answer: str = Field(..., max_length=1500)
    citations: list[Citation]
    tool_calls: list[ToolCallRecord]
    mode: Literal["mock", "real"]
    latency_ms: int
    trace: list[NodeTiming] = Field(default_factory=list)

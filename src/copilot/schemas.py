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

    question: str = Field(..., min_length=1)
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

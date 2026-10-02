"""
LangGraph state for the copilot graph.

The router computes the full ordered `pending_tools` list once; each tool
node pops itself off the front. A bounded worker queue rather than a
parallel fan-out: deterministic ordering, no merge conflicts, and a fixed
number of LLM calls per request (router + synthesis, plus at most one
verification retry) however many tools fire.
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class CopilotState(TypedDict):
    question: str
    tickers: list[str]
    fiscal_year: int | None
    metrics: list[str]
    as_of: str | None
    positions: dict[str, float]
    locale: str
    mock: bool

    pending_tools: list[str]
    route_reasoning: str

    # Reducers: each node returns only its own contribution.
    tool_calls: Annotated[list[dict[str, Any]], operator.add]
    evidence: Annotated[list[dict[str, Any]], operator.add]
    findings: Annotated[dict[str, list[str]], operator.or_]
    gaps: Annotated[list[str], operator.add]

    answer: str | None
    verification: dict[str, Any] | None


def initial_state(
    question: str,
    tickers: list[str] | None = None,
    as_of: str | None = None,
    positions: dict[str, float] | None = None,
    locale: str = "en",
    mock: bool = True,
) -> CopilotState:
    return CopilotState(
        question=question,
        tickers=list(tickers or []),
        fiscal_year=None,
        metrics=[],
        as_of=as_of,
        positions=dict(positions or {}),
        locale=locale,
        mock=mock,
        pending_tools=[],
        route_reasoning="",
        tool_calls=[],
        evidence=[],
        findings={},
        gaps=[],
        answer=None,
        verification=None,
    )

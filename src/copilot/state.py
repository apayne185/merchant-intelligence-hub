"""
LangGraph state schema for the copilot's orchestrator graph (graph.py).

Design: `route` computes the full ordered `pending_tools` list once, then
each specialist node pops its own name off the front and hands off to the
next — a bounded worker-queue, not a parallel fan-out. See DECISIONS.md D26
for why (deterministic ordering, no reducer/merge-conflict surface to
reason about, a fixed cap on real-mode LLM calls per request regardless of
how many tools fire).
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class CopilotState(TypedDict):
    question: str
    merchant_id: int | None
    locale: str
    mock: bool

    # Set once by the router node, then overwritten (not appended to) by
    # each specialist node as it pops its own name off the front.
    pending_tools: list[str]
    route_reasoning: str

    # Annotated with reducers so each node returns only its own
    # contribution and LangGraph merges it automatically, rather than every
    # node needing to know how to merge with whatever the previous node
    # already produced. operator.add concatenates lists; operator.or_
    # merges dicts (Python 3.9+ dict.__or__).
    tool_calls: Annotated[list[dict[str, Any]], operator.add]
    citations: Annotated[list[dict[str, Any]], operator.add]
    tool_results: Annotated[dict[str, Any], operator.or_]

    answer: str | None


def initial_state(
    question: str,
    merchant_id: int | None = None,
    locale: str = "en",
    mock: bool = True,
) -> CopilotState:
    """Constructs a valid starting state so callers (the API, the eval
    harness, tests) don't have to remember every field."""
    return CopilotState(
        question=question,
        merchant_id=merchant_id,
        locale=locale,
        mock=mock,
        pending_tools=[],
        route_reasoning="",
        tool_calls=[],
        citations=[],
        tool_results={},
        answer=None,
    )

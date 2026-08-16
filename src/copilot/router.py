"""
Router — decides which specialist tool(s) a question needs.

Mock mode: deterministic keyword rules, zero LLM calls — same style as
_MockAgent in src/parte4_api/agent.py. Real mode: an Agno agent with
output_schema=RouteDecision and NO tools attached — classification +
argument extraction only, mirroring agent.py's _LLMClassification pattern
(D9). See DECISIONS.md D26.
"""
from __future__ import annotations

import re

from src.copilot.schemas import RouteDecision, ToolName
from src.copilot.state import CopilotState

_DATA_PATTERNS = re.compile(
    r"\b(tpv|top merchants?|approval rate|yoy|year.over.year|volume|kpi|churn rate)\b", re.IGNORECASE
)
_RISK_PATTERNS = re.compile(
    r"\b(risk|churn|at.?risk|trending|predict|likely to (leave|cancel))\b", re.IGNORECASE
)
_GROUNDING_PATTERNS = re.compile(
    r"\b(polic(y|ies)|onboarding|kyc|escalat|require[sd]?|compliance|\bsla\b)\b", re.IGNORECASE
)
_COMPLAINT_PATTERNS = re.compile(
    r"\b(cancel|complain|not working|broken|charged (me|twice)|refund)\b", re.IGNORECASE
)


def route_mock(question: str, merchant_id: int | None) -> list[ToolName]:
    """Deterministic keyword router — zero LLM calls. A question can match
    multiple patterns (the flagship "trending toward churn ... does policy
    flag them" question matches both risk and grounding) — all matches are
    kept, in a fixed order (data_analyst, risk, grounding,
    complaint_classifier) so tool_calls order is reproducible.
    """
    tools: list[ToolName] = []
    if _DATA_PATTERNS.search(question):
        tools.append("data_analyst")

    if _RISK_PATTERNS.search(question):
        tools.append("risk")
        # A per-merchant risk question is better answered with concrete KPI
        # evidence alongside the ML score than the score alone — the
        # model's own discrimination is weak (DECISIONS.md D24), so lean on
        # data_analyst's YoY/TPV facts as the more reliable grounding.
        if merchant_id is not None and "data_analyst" not in tools:
            tools.append("data_analyst")

    if _GROUNDING_PATTERNS.search(question):
        tools.append("grounding")

    # Complaint classifier only fires when nothing analytical matched AND
    # the text reads like a first-person complaint with a known merchant —
    # see tools/complaint_classifier.py's module docstring for why this
    # stays conservative (misrouting an analytical question here silently
    # misclassifies it as `other`/low-urgency instead of answering it).
    if not tools and merchant_id is not None and _COMPLAINT_PATTERNS.search(question):
        tools.append("complaint_classifier")

    if not tools:
        # No confident match — default to grounding rather than answering
        # nothing; policy context is the safest fallback for an ambiguous
        # business question.
        tools.append("grounding")

    return tools


def route_real(question: str, merchant_id: int | None) -> RouteDecision:
    """Real-mode router: an Agno agent, output_schema=RouteDecision, no
    tools attached — classification + argument extraction only."""
    from agno.agent import Agent
    from agno.models.openai import OpenAIChat

    instructions = f"""
You are the router for a merchant-intelligence copilot. Given a user's
question, decide which specialist tools are needed to answer it:

- data_analyst: concrete KPI/SQL facts (TPV, approval rate, YoY, top merchants)
- risk: the churn-risk ML model's score/drivers for specific merchant(s)
- grounding: company policy/onboarding/escalation documents
- complaint_classifier: the question IS a pasted customer complaint, not an
  analytical question about merchants in general

A question can need more than one tool. The caller already knows
merchant_id={merchant_id} if set; only fill `merchant_id` in your response
if you can extract one directly from the question text that the caller
didn't already supply.
"""
    agent = Agent(
        model=OpenAIChat(id="gpt-4o-mini"),
        instructions=instructions,
        output_schema=RouteDecision,
        structured_outputs=True,
    )
    run_output = agent.run(question)
    content = run_output.content
    if isinstance(content, RouteDecision):
        return content
    if isinstance(content, dict):
        return RouteDecision(**content)
    raise TypeError(f"Unexpected router response: {type(content)!r}")


def router_node(state: CopilotState) -> dict:
    """LangGraph node: populates pending_tools (+ merchant_id, if the real
    router extracted one the caller didn't already supply) from the
    question. Branches on state['mock'] rather than reading MOCK_LLM from
    the environment directly, so mode is explicit and traceable through the
    graph state instead of implicit global process state.
    """
    if state["mock"]:
        tools = route_mock(state["question"], state["merchant_id"])
        reasoning = "mock keyword router"
        merchant_id = state["merchant_id"]
    else:
        decision = route_real(state["question"], state["merchant_id"])
        tools = decision.tools or ["grounding"]
        reasoning = decision.reasoning
        merchant_id = state["merchant_id"] if state["merchant_id"] is not None else decision.merchant_id

    return {
        "pending_tools": tools,
        "route_reasoning": reasoning,
        "merchant_id": merchant_id,
    }

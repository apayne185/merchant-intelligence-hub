"""
Tests for src/copilot/graph.py — end-to-end orchestrator invocations, mock
mode only (zero LLM calls). Uses the force_fixture_csv fixture
(tests/conftest.py) so results are deterministic regardless of whether the
real (gitignored) transactions_sample.csv happens to be present locally —
CI never has it, so tests must not depend on it either.
"""
from __future__ import annotations

import pytest
from src.copilot.graph import build_graph, pick_next
from src.copilot.state import initial_state
from src.copilot.tools.grounding import known_policy_ids


@pytest.fixture
def graph(force_fixture_csv: None):
    return build_graph()


# ---------------------------------------------------------------------------
# pick_next — pure function, no graph/LLM needed
# ---------------------------------------------------------------------------
def test_pick_next_returns_first_pending_tool() -> None:
    state = initial_state("q")
    state["pending_tools"] = ["risk", "grounding"]
    assert pick_next(state) == "risk"


def test_pick_next_returns_synthesize_when_empty() -> None:
    state = initial_state("q")
    state["pending_tools"] = []
    assert pick_next(state) == "synthesize"


# ---------------------------------------------------------------------------
# End-to-end graph.invoke()
# ---------------------------------------------------------------------------
def test_flagship_question_routes_risk_and_grounding_with_citations(graph) -> None:
    state = initial_state(
        "Which merchants are trending toward churn and why, and does anything "
        "in our onboarding policy flag them?",
        mock=True,
    )
    result = graph.invoke(state)
    fired_tools = {tc["tool"] for tc in result["tool_calls"]}
    assert fired_tools == {"risk", "grounding"}
    assert any(c["source_type"] == "model_output" for c in result["citations"])
    assert any(c["source_type"] == "policy_doc" for c in result["citations"])
    assert "0.58" in result["answer"]


def test_merchant_specific_risk_question_includes_yoy_evidence(graph) -> None:
    state = initial_state("Is this merchant at risk of churning and why?", merchant_id=90001, mock=True)
    result = graph.invoke(state)
    assert "90001" in result["answer"]
    assert "%" in result["answer"]  # concrete KPI evidence alongside the model's score


def test_unknown_merchant_id_does_not_crash(graph) -> None:
    state = initial_state("why is this merchant at risk", merchant_id=999999, mock=True)
    result = graph.invoke(state)
    assert "No transaction history found for merchant 999999" in result["answer"]


def test_complaint_routes_only_to_classifier(graph) -> None:
    state = initial_state("I want to cancel my account, this is unacceptable.", merchant_id=90001, mock=True)
    result = graph.invoke(state)
    assert {tc["tool"] for tc in result["tool_calls"]} == {"complaint_classifier"}
    assert result["tool_results"]["complaint_classifier"]["category"] == "churn_threat"


def test_no_matching_route_still_returns_an_answer(graph) -> None:
    state = initial_state("hello there", mock=True)
    result = graph.invoke(state)
    assert result["answer"]


def test_citations_only_reference_known_policy_ids(graph) -> None:
    state = initial_state("What does onboarding require?", mock=True)
    result = graph.invoke(state)
    known = known_policy_ids()
    policy_citations = [c for c in result["citations"] if c["source_type"] == "policy_doc"]
    assert policy_citations
    assert all(c["id"] in known for c in policy_citations)


def test_pure_data_analyst_question_does_not_call_risk_or_grounding(graph) -> None:
    state = initial_state("What is the top merchant by TPV and the approval rate by segment?", mock=True)
    result = graph.invoke(state)
    assert {tc["tool"] for tc in result["tool_calls"]} == {"data_analyst"}


def test_graph_invocation_is_deterministic(graph) -> None:
    state = initial_state("Which merchants are at risk?", mock=True)
    r1 = graph.invoke(state)
    r2 = graph.invoke(initial_state("Which merchants are at risk?", mock=True))
    assert r1["answer"] == r2["answer"]
    assert r1["tool_calls"] == r2["tool_calls"]

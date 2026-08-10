"""
Tests for src/copilot/router.py.

Mock-mode tests (route_mock) need no MOCK_LLM/API key. Real-mode tests
(route_real) mock agno's Agent.run — same pattern as
tests/test_agent_adapter.py — no live OpenAI call.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from src.copilot.router import route_mock, route_real
from src.copilot.schemas import RouteDecision


# ---------------------------------------------------------------------------
# route_mock
# ---------------------------------------------------------------------------
def test_route_mock_data_analyst_keywords() -> None:
    assert "data_analyst" in route_mock("what is the TPV for top merchants this quarter?", None)


def test_route_mock_risk_keywords() -> None:
    assert "risk" in route_mock("which merchants are trending toward churn?", None)


def test_route_mock_churn_rate_question_routes_data_analyst() -> None:
    # "churn rate by segment" is an aggregate-stats question that
    # data_analyst.churn_rate_by_segment() directly answers — plain "churn"
    # alone would only fire the risk (per-merchant scoring) pattern, which
    # doesn't answer "what's the rate", so data_analyst must fire too.
    assert "data_analyst" in route_mock("what is the churn rate by segment?", None)


def test_route_mock_grounding_keywords() -> None:
    assert "grounding" in route_mock("what does our onboarding policy require?", None)


def test_route_mock_flagship_question_routes_risk_and_grounding() -> None:
    tools = route_mock(
        "Which merchants are trending toward churn and why, and does anything "
        "in our onboarding policy flag them?",
        None,
    )
    assert set(tools) == {"risk", "grounding"}


def test_route_mock_risk_with_merchant_id_also_pulls_data_analyst() -> None:
    tools = route_mock("is this merchant at risk?", merchant_id=90001)
    assert set(tools) == {"risk", "data_analyst"}


def test_route_mock_risk_without_merchant_id_does_not_force_data_analyst() -> None:
    assert route_mock("which merchants are at risk?", merchant_id=None) == ["risk"]


def test_route_mock_complaint_requires_merchant_id() -> None:
    text = "I want to cancel my account, this is unacceptable."
    assert route_mock(text, merchant_id=90001) == ["complaint_classifier"]
    assert route_mock(text, merchant_id=None) != ["complaint_classifier"]


def test_route_mock_no_match_defaults_to_grounding() -> None:
    assert route_mock("hello there", None) == ["grounding"]


def test_route_mock_order_is_fixed_regardless_of_keyword_order() -> None:
    tools = route_mock("what about our onboarding policy and the TPV trend risk?", merchant_id=1)
    assert tools.index("data_analyst") < tools.index("risk") < tools.index("grounding")


# ---------------------------------------------------------------------------
# route_real
# ---------------------------------------------------------------------------
class _FakeRunOutput:
    def __init__(self, content) -> None:
        self.content = content


@pytest.fixture
def real_agent_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")


def test_route_real_handles_typed_route_decision(real_agent_env: None) -> None:
    fake = RouteDecision(tools=["risk", "grounding"], merchant_id=90001, reasoning="test reasoning")
    with patch("agno.agent.Agent.run", return_value=_FakeRunOutput(fake)):
        decision = route_real("some question", None)
    assert decision.tools == ["risk", "grounding"]
    assert decision.merchant_id == 90001


def test_route_real_handles_plain_dict_content(real_agent_env: None) -> None:
    dict_content = {"tools": ["grounding"], "merchant_id": None, "reasoning": "x"}
    with patch("agno.agent.Agent.run", return_value=_FakeRunOutput(dict_content)):
        decision = route_real("q", None)
    assert decision.tools == ["grounding"]

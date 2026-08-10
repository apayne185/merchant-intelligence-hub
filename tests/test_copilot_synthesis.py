"""Tests for src/copilot/synthesis.py — mock-mode templating (no LLM calls)."""
from __future__ import annotations

from src.copilot.state import initial_state
from src.copilot.synthesis import synthesize_mock


def test_synthesize_mock_empty_results_returns_fallback_message() -> None:
    state = initial_state("q")
    assert synthesize_mock(state) == "No information was found for this question."


def test_synthesize_mock_risk_found_includes_caveat_and_tier() -> None:
    state = initial_state("q")
    state["tool_results"] = {
        "risk": {
            "scores": [{
                "merchant_id": 1,
                "found": True,
                "churn_probability": 0.5,
                "risk_tier": "high",
                "top_drivers": [{"feature": "tpv_total"}],
                "caveat": "SOME_CAVEAT_TEXT",
            }],
        }
    }
    answer = synthesize_mock(state)
    assert "SOME_CAVEAT_TEXT" in answer
    assert "high" in answer
    assert "tpv_total" in answer


def test_synthesize_mock_risk_not_found() -> None:
    # score_merchant() always includes "caveat" even on found=False (see
    # src/copilot/tools/risk.py) — matching that real shape here, not a
    # hypothetical one, since _summarize_risk relies on it being present.
    state = initial_state("q")
    state["tool_results"] = {
        "risk": {"scores": [{"merchant_id": 5, "found": False, "caveat": "SOME_CAVEAT_TEXT"}]}
    }
    assert "No transaction history found for merchant 5" in synthesize_mock(state)


def test_synthesize_mock_grounding_cites_policy() -> None:
    state = initial_state("q")
    state["tool_results"] = {"grounding": [{"id": "RP-01", "title": "T", "text": "policy text"}]}
    answer = synthesize_mock(state)
    assert "RP-01" in answer
    assert "policy text" in answer


def test_synthesize_mock_complaint_classifier() -> None:
    state = initial_state("q")
    state["tool_results"] = {
        "complaint_classifier": {"category": "billing", "urgency": 2, "reasoning": "duplicate charge"}
    }
    answer = synthesize_mock(state)
    assert "billing" in answer
    assert "duplicate charge" in answer


def test_synthesize_mock_data_analyst_top_merchant() -> None:
    state = initial_state("q")
    state["tool_results"] = {
        "data_analyst": {"top_merchants_by_tpv": [{"merchant_id": 42, "tpv": 100.0, "approval_rate": 0.9}]},
    }
    assert "42" in synthesize_mock(state)


def test_synthesize_mock_combines_multiple_tools_in_fixed_order() -> None:
    state = initial_state("q")
    state["tool_results"] = {
        "grounding": [{"id": "RP-01", "title": "T", "text": "policy text"}],
        "risk": {"scores": [{"merchant_id": 1, "found": False, "caveat": "SOME_CAVEAT_TEXT"}]},
    }
    answer = synthesize_mock(state)
    # risk summarized before grounding regardless of dict insertion order —
    # fixed tool order (data_analyst, risk, grounding, complaint_classifier).
    assert answer.index("No transaction history") < answer.index("RP-01")


def test_synthesize_mock_is_deterministic() -> None:
    state = initial_state("q")
    state["tool_results"] = {"grounding": [{"id": "RP-01", "title": "T", "text": "x"}]}
    assert synthesize_mock(state) == synthesize_mock(state)

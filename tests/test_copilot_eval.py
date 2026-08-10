"""
Tests for the copilot eval harness (scripts/evaluate_copilot.py).

Runs in mock mode (deterministic, no OPENAI_API_KEY). data/golden_set_copilot.json
was built by verifying actual router/graph output first (see DECISIONS.md
D28) — these tests check shape/invariants and lock in that verified
baseline as a regression guard, not aspirational numbers.
"""
from __future__ import annotations

from scripts.evaluate_copilot import evaluate


def test_evaluate_returns_expected_shape() -> None:
    report = evaluate(mock=True)
    assert report["mode"] == "mock"
    assert report["n_examples"] == 11
    assert len(report["results"]) == report["n_examples"]
    assert 0.0 <= report["route_exact_match_rate"] <= 1.0
    assert 0.0 <= report["route_recall_mean"] <= 1.0
    assert 0.0 <= report["citation_hallucination_rate"] <= 1.0
    assert 0.0 <= report["facts_ok_rate"] <= 1.0


def test_evaluate_citation_hallucination_rate_is_zero() -> None:
    # The single highest-value regression check for a RAG system: every
    # cited policy_doc id must actually exist in data/policy_docs.json.
    report = evaluate(mock=True)
    assert report["citation_hallucination_rate"] == 0.0


def test_evaluate_route_exact_match_is_perfect_on_the_verified_baseline() -> None:
    # The golden set's expected_route values were derived from actually
    # running each question through the graph (D28) — a regression here
    # means router or tool behavior changed, not that the golden set was
    # wrong.
    report = evaluate(mock=True)
    assert report["route_exact_match_rate"] == 1.0


def test_evaluate_risk_caveat_mention_rate_is_perfect() -> None:
    # Every example expecting the model's ROC-AUC caveat must get it —
    # this is the mechanical check for D24's honesty concern.
    report = evaluate(mock=True)
    assert report["risk_caveat_mention_rate"] == 1.0


def test_evaluate_classification_accuracy_is_perfect() -> None:
    report = evaluate(mock=True)
    assert report["classification_accuracy"] == 1.0


def test_evaluate_results_have_expected_fields() -> None:
    report = evaluate(mock=True)
    for r in report["results"]:
        assert {
            "id",
            "question",
            "expected_route",
            "predicted_route",
            "route_exact_match",
            "route_recall",
            "facts_ok",
            "classification_ok",
        }.issubset(r.keys())
        assert isinstance(r["route_exact_match"], bool)
        assert isinstance(r["facts_ok"], bool)


def test_evaluate_is_deterministic_across_runs() -> None:
    first = evaluate(mock=True)
    second = evaluate(mock=True)
    assert first == second

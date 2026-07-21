"""
Tests para el harness de evaluación (scripts/evaluate_classifier.py).

Corre en modo mock (determinístico, sin OPENAI_API_KEY). No se afirman
números exactos de accuracy del _MockAgent (es un stub basado en reglas, no
un clasificador real — ver DECISIONS.md D21) — solo que el harness produce
un reporte con la forma y las invariantes correctas.
"""
from __future__ import annotations

from scripts.evaluate_classifier import evaluate


def test_evaluate_returns_expected_shape() -> None:
    report = evaluate(mock=True)
    assert report["mode"] == "mock"
    assert report["n_examples"] > 0
    assert 0.0 <= report["overall_accuracy"] <= 1.0
    assert len(report["results"]) == report["n_examples"]


def test_evaluate_per_category_accuracy_covers_all_six_categories() -> None:
    report = evaluate(mock=True)
    expected_categories = {
        "technical_issue",
        "billing",
        "onboarding",
        "fraud",
        "churn_threat",
        "other",
    }
    assert expected_categories.issubset(report["per_category_accuracy"].keys())
    for acc in report["per_category_accuracy"].values():
        assert 0.0 <= acc <= 1.0


def test_evaluate_prompt_injection_detection_rate_is_perfect() -> None:
    # The prompt-injection guardrail is deterministic and checked before any
    # LLM/mock classification logic runs — it should catch 100% of the
    # golden set's injection examples regardless of language.
    report = evaluate(mock=True)
    assert report["prompt_injection_detection_rate"] == 1.0


def test_evaluate_retrieval_precision_is_reasonably_high() -> None:
    # Not a strict correctness assertion (TF-IDF is a lexical stand-in, not
    # semantic search) — just a regression guard that retrieval quality
    # doesn't silently collapse if the corpus or dedup/budget logic changes.
    report = evaluate(mock=True)
    assert report["retrieval_category_precision_at_k"] > 0.5


def test_evaluate_results_have_expected_fields() -> None:
    report = evaluate(mock=True)
    for r in report["results"]:
        assert {
            "id",
            "expected_category",
            "predicted_category",
            "correct",
            "urgency_meets_minimum",
            "escalation_correct",
        }.issubset(r.keys())
        assert isinstance(r["correct"], bool)
        assert isinstance(r["urgency_meets_minimum"], bool)
        assert isinstance(r["escalation_correct"], bool)


def test_evaluate_reports_urgency_and_escalation_metrics() -> None:
    # golden_set.json labels every example with expected_min_urgency and
    # expected_requires_escalation — these must actually be checked, not
    # just carried as unused fields in the data.
    report = evaluate(mock=True)
    assert 0.0 <= report["urgency_meets_minimum_rate"] <= 1.0
    assert 0.0 <= report["escalation_accuracy"] <= 1.0


def test_evaluate_is_deterministic_across_runs() -> None:
    first = evaluate(mock=True)
    second = evaluate(mock=True)
    assert first == second

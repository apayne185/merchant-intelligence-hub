"""
Tests for scripts/evaluate_retrieval.py — the mock-mode retrieval-quality
benchmark (recall@k / MRR) over the policy corpus. Real-mode (OpenAI
embeddings) isn't exercised here — same reasoning as
tests/test_copilot_grounding.py's mock-only coverage: no network calls/API
key in CI.
"""
from __future__ import annotations

import pytest
from scripts.evaluate_retrieval import _recall_at_k, _reciprocal_rank, evaluate


def test_reciprocal_rank_hit_at_first_position() -> None:
    assert _reciprocal_rank(["RP-01", "RP-02"], {"RP-01"}) == 1.0


def test_reciprocal_rank_hit_at_third_position() -> None:
    assert _reciprocal_rank(["RP-02", "RP-03", "RP-01"], {"RP-01"}) == pytest.approx(1 / 3)


def test_reciprocal_rank_no_hit_returns_zero() -> None:
    assert _reciprocal_rank(["RP-02", "RP-03"], {"RP-01"}) == 0.0


def test_recall_at_k_full_hit() -> None:
    assert _recall_at_k(["RP-01", "RP-02"], {"RP-01"}, k=2) == 1.0


def test_recall_at_k_respects_k_cutoff() -> None:
    assert _recall_at_k(["RP-02", "RP-01"], {"RP-01"}, k=1) == 0.0


def test_recall_at_k_empty_expected_is_vacuously_full_recall() -> None:
    assert _recall_at_k(["RP-01"], set(), k=1) == 1.0


def test_evaluate_mock_covers_full_golden_set() -> None:
    report = evaluate(mock=True)
    assert report["n_queries"] == 15
    assert report["corpus_size"] == 15
    assert len(report["per_query"]) == 15


def test_evaluate_mock_recall_at_3_is_perfect() -> None:
    # Documented floor for the mock (TF-IDF) embedder — see DECISIONS.md D36.
    # A drop here means a retrieval regression, not embedder noise: TF-IDF
    # over this fixed 15-doc corpus is fully deterministic.
    report = evaluate(mock=True)
    assert report["recall_at_k"]["3"] == 1.0


def test_evaluate_mock_mrr_meets_documented_floor() -> None:
    report = evaluate(mock=True)
    assert report["mrr"] >= 0.9


def test_evaluate_per_query_reports_expected_shape() -> None:
    report = evaluate(mock=True)
    example = report["per_query"][0]
    assert {"query", "expected_doc_ids", "top_5_retrieved", "reciprocal_rank", "recall_at_k", "category_precision_at_k"}.issubset(
        example.keys()
    )

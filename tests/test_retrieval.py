"""
Tests para el módulo de retrieval (src/parte4_api/retrieval.py).

Todo corre en modo mock (TF-IDF, sin llamadas a OpenAI) — no requiere
OPENAI_API_KEY.
"""
from __future__ import annotations

import numpy as np
import pytest

from src.parte4_api.retrieval import (
    SimpleVectorStore,
    _dedupe_by_resolution,
    _fit_to_budget,
    retrieve_similar_cases,
)


# ---------------------------------------------------------------------------
# SimpleVectorStore
# ---------------------------------------------------------------------------
def test_vector_store_empty_query_returns_empty_list() -> None:
    store = SimpleVectorStore()
    assert store.query(np.array([1.0, 0.0]), k=3) == []
    assert len(store) == 0


def test_vector_store_returns_most_similar_first() -> None:
    store = SimpleVectorStore()
    records = [{"id": 1}, {"id": 2}, {"id": 3}]
    vectors = np.array([
        [1.0, 0.0],
        [0.0, 1.0],
        [0.9, 0.1],
    ])
    store.add(records, vectors)

    results = store.query(np.array([1.0, 0.0]), k=2)
    assert [r["id"] for r in results] == [1, 3]


def test_vector_store_ties_break_deterministically_by_original_index() -> None:
    # Two records tied at identical cosine similarity to the query — the
    # result order must be reproducible run-to-run (ascending by original
    # index), not left to numpy's internal argsort tie-breaking.
    store = SimpleVectorStore()
    records = [{"id": 1}, {"id": 2}, {"id": 3}]
    vectors = np.array([
        [1.0, 0.0],  # tied with id=2
        [1.0, 0.0],  # tied with id=1
        [0.0, 1.0],  # not tied, least similar
    ])
    store.add(records, vectors)

    results = store.query(np.array([1.0, 0.0]), k=2)
    assert [r["id"] for r in results] == [1, 2]


def test_vector_store_k_larger_than_corpus_does_not_crash() -> None:
    store = SimpleVectorStore()
    store.add([{"id": 1}], np.array([[1.0, 0.0]]))
    results = store.query(np.array([1.0, 0.0]), k=50)
    assert len(results) == 1


# ---------------------------------------------------------------------------
# retrieve_similar_cases (mock mode — TF-IDF, no network calls)
# ---------------------------------------------------------------------------
def test_retrieve_similar_cases_returns_expected_shape() -> None:
    results = retrieve_similar_cases("El POS se reinicia solo", k=3, mock=True)
    assert len(results) <= 3
    for r in results:
        assert {"category", "urgency", "resolution_notes"}.issubset(r.keys())


def test_retrieve_similar_cases_respects_k() -> None:
    results = retrieve_similar_cases("problema con el terminal", k=1, mock=True)
    assert len(results) == 1


def test_retrieve_similar_cases_churn_query_surfaces_churn_case() -> None:
    results = retrieve_similar_cases("Cancelen mi cuenta ya, esto no funciona", k=3, mock=True)
    categories = [r["category"] for r in results]
    assert "churn_threat" in categories


@pytest.mark.parametrize("k", [0, -1])
def test_retrieve_similar_cases_non_positive_k_returns_empty(k: int) -> None:
    assert retrieve_similar_cases("cualquier texto", k=k, mock=True) == []


def test_retrieve_similar_cases_respects_context_char_budget() -> None:
    results = retrieve_similar_cases(
        "El POS se reinicia solo", k=3, mock=True, max_context_chars=50
    )
    total_chars = sum(len(r["resolution_notes"]) for r in results)
    assert total_chars <= 50


def test_retrieve_similar_cases_zero_budget_returns_no_cases() -> None:
    results = retrieve_similar_cases(
        "El POS se reinicia solo", k=3, mock=True, max_context_chars=0
    )
    assert results == []


# ---------------------------------------------------------------------------
# Context-window management helpers
# ---------------------------------------------------------------------------
def test_dedupe_by_resolution_drops_identical_notes() -> None:
    records = [
        {"category": "a", "resolution_notes": "Same fix applied."},
        {"category": "b", "resolution_notes": "Same fix applied."},
        {"category": "c", "resolution_notes": "Different fix."},
    ]
    deduped = _dedupe_by_resolution(records)
    assert len(deduped) == 2
    assert [r["category"] for r in deduped] == ["a", "c"]


def test_dedupe_by_resolution_is_case_and_whitespace_insensitive() -> None:
    records = [
        {"category": "a", "resolution_notes": "  Same Fix Applied.  "},
        {"category": "b", "resolution_notes": "same fix applied."},
    ]
    assert len(_dedupe_by_resolution(records)) == 1


def test_fit_to_budget_truncates_with_ellipsis() -> None:
    records = [{"resolution_notes": "x" * 100}]
    fitted = _fit_to_budget(records, max_chars=10)
    assert len(fitted) == 1
    assert len(fitted[0]["resolution_notes"]) == 10
    assert fitted[0]["resolution_notes"].endswith("…")


def test_fit_to_budget_drops_cases_once_budget_exhausted() -> None:
    records = [
        {"resolution_notes": "x" * 20},
        {"resolution_notes": "y" * 20},
        {"resolution_notes": "z" * 20},
    ]
    fitted = _fit_to_budget(records, max_chars=25)
    assert len(fitted) < len(records)
    assert sum(len(r["resolution_notes"]) for r in fitted) <= 25

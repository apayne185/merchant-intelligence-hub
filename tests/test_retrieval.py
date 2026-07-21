"""
Tests para el módulo de retrieval (src/parte4_api/retrieval.py).

Todo corre en modo mock (TF-IDF, sin llamadas a OpenAI) — no requiere
OPENAI_API_KEY.
"""
from __future__ import annotations

import numpy as np
import pytest

from src.parte4_api.retrieval import SimpleVectorStore, retrieve_similar_cases


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

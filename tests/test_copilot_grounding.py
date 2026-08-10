"""
Tests for the Grounding tool (src/copilot/tools/grounding.py).

Runs in mock mode (TF-IDF, no network calls) — mirrors tests/test_retrieval.py
for the historical-complaints corpus, but for data/policy_docs.json.
"""
from __future__ import annotations

import pytest
from src.copilot.tools.grounding import known_policy_ids, retrieve_policy


def test_known_policy_ids_has_all_fifteen_docs() -> None:
    ids = known_policy_ids()
    assert len(ids) == 15
    assert "RP-01" in ids
    assert "RP-15" in ids


def test_retrieve_policy_returns_expected_shape() -> None:
    results = retrieve_policy("onboarding documents required for a new merchant", k=3, mock=True)
    assert 1 <= len(results) <= 3
    for r in results:
        assert {"id", "title", "category", "text"}.issubset(r.keys())
        assert r["id"] in known_policy_ids()


def test_retrieve_policy_onboarding_query_surfaces_onboarding_doc() -> None:
    results = retrieve_policy("What does onboarding require before activating a new SMB merchant?", k=2, mock=True)
    ids = [r["id"] for r in results]
    assert "RP-01" in ids


def test_retrieve_policy_churn_query_surfaces_churn_escalation_doc() -> None:
    results = retrieve_policy("a merchant is showing high churn risk, what should we do?", k=2, mock=True)
    ids = [r["id"] for r in results]
    assert "RP-04" in ids


def test_retrieve_policy_respects_k() -> None:
    results = retrieve_policy("policy", k=1, mock=True)
    assert len(results) == 1


@pytest.mark.parametrize("k", [0, -1])
def test_retrieve_policy_non_positive_k_returns_empty(k: int) -> None:
    assert retrieve_policy("anything", k=k, mock=True) == []


def test_retrieve_policy_respects_context_char_budget() -> None:
    results = retrieve_policy("onboarding", k=3, mock=True, max_context_chars=50)
    total_chars = sum(len(r["text"]) for r in results)
    assert total_chars <= 50

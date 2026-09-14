"""
Tests for the Grounding tool (src/copilot/tools/grounding.py).

Runs in mock mode (TF-IDF, no network calls) — mirrors tests/test_retrieval.py
for the historical-complaints corpus, but for data/policy_docs.json.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from src.copilot.retrieval_core import _CORPUS_STORE_CACHE
from src.copilot.tools import ingestion as ingestion_module
from src.copilot.tools.grounding import CORPUS_NAME, known_policy_ids, retrieve_policy
from src.copilot.tools.ingestion import ingest_and_index

from tests.test_copilot_ingestion import _build_pdf


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


# -----------------------------------------------------------------------------
# Merge with src.copilot.tools.ingestion's data/ingested_docs/*.json — see
# DECISIONS.md D38. INGESTED_DOCS_DIR is monkeypatched per test to an empty
# or populated tmp_path, and the shared ("policy_docs", True) corpus-store
# cache entry (D19) is reset before/after so these tests don't leak a
# stale merged store into the "exactly 15 docs" tests above (which assume
# no ingested docs exist), and vice versa.
# -----------------------------------------------------------------------------
@pytest.fixture
def _isolated_ingested_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # grounding._load_policy_docs() reads ingestion_module.INGESTED_DOCS_DIR
    # via the module object (not a bare-name import) specifically so this
    # monkeypatch is visible to it — see _load_policy_docs()'s docstring.
    monkeypatch.setattr(ingestion_module, "INGESTED_DOCS_DIR", tmp_path / "ingested_docs")
    _CORPUS_STORE_CACHE.pop((CORPUS_NAME, True), None)
    yield tmp_path / "ingested_docs"
    _CORPUS_STORE_CACHE.pop((CORPUS_NAME, True), None)


def test_retrieve_policy_finds_ingested_pdf_content(_isolated_ingested_dir: Path) -> None:
    pdf = _build_pdf(_isolated_ingested_dir.parent, ["Refunds are processed within 5 business days of approval."])
    ingest_and_index(pdf, doc_id_prefix="RFD", title="Refund Timeline Addendum")

    results = retrieve_policy("how many days to process a refund", k=3, mock=True)
    ids = [r["id"] for r in results]
    assert "RFD-001" in ids


def test_known_policy_ids_includes_ingested_docs(_isolated_ingested_dir: Path) -> None:
    pdf = _build_pdf(_isolated_ingested_dir.parent, ["Some ingested addendum text."])
    ingest_and_index(pdf, doc_id_prefix="ADD", title="Addendum")

    ids = known_policy_ids()
    assert "ADD-001" in ids
    # The 15 hand-written docs are still present alongside the ingested one
    # — merge, not replace.
    assert "RP-01" in ids
    assert len(ids) == 16


def test_grounding_falls_back_to_policy_docs_only_when_ingested_dir_missing(
    _isolated_ingested_dir: Path,
) -> None:
    # _isolated_ingested_dir points at a tmp_path subdirectory that's never
    # created in this test — confirms _load_policy_docs()'s `.exists()`
    # guard, not just that the monkeypatch took effect.
    assert not _isolated_ingested_dir.exists()
    ids = known_policy_ids()
    assert len(ids) == 15

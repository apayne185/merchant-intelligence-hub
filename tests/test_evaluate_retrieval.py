"""
Tests for scripts/evaluate_retrieval.py — the mock-mode retrieval-quality
benchmark (recall@k / MRR) over the policy corpus. Real-mode (OpenAI
embeddings) isn't exercised here — same reasoning as
tests/test_copilot_grounding.py's mock-only coverage: no network calls/API
key in CI.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from scripts.evaluate_retrieval import _describe_embedder, _recall_at_k, _reciprocal_rank, evaluate
from src.copilot.retrieval_core import AzureOpenAIEmbedder, MockEmbedder, OpenAIEmbedder


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


# -----------------------------------------------------------------------------
# _describe_embedder — the report's "mode" label is derived from the actual
# embedder instance, not hardcoded per mock/real bool. Before this fix,
# --real always printed "real (OpenAI text-embedding-3-small)" even when
# retrieval_core._select_real_embedder() (D39) actually routed to Azure.
# -----------------------------------------------------------------------------
def test_describe_embedder_mock() -> None:
    assert _describe_embedder(MockEmbedder(["some text"])) == "MockEmbedder"


def test_describe_embedder_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    assert _describe_embedder(OpenAIEmbedder()) == "OpenAIEmbedder (text-embedding-3-small)"


def test_describe_embedder_azure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("OPENAI_API_VERSION", "2024-02-01")
    with patch("openai.AzureOpenAI"):
        embedder = AzureOpenAIEmbedder()
    assert _describe_embedder(embedder) == "AzureOpenAIEmbedder (text-embedding-3-small)"


def test_evaluate_mock_report_mode_reflects_mock_embedder() -> None:
    report = evaluate(mock=True)
    assert report["mode"] == "MockEmbedder"


# -----------------------------------------------------------------------------
# Corpus pinning — evaluate() must read ONLY data/policy_docs.json, not
# grounding._load_policy_docs's merged corpus (+ data/ingested_docs/*.json,
# D38). Without this pin, ingesting any PDF would silently shift
# corpus_size/category_precision_at_k against the committed baseline.
# -----------------------------------------------------------------------------
def test_evaluate_corpus_unaffected_by_ingested_docs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    import src.copilot.tools.ingestion as ingestion_module
    from scripts.evaluate_retrieval import _PINNED_CORPUS_NAME
    from src.copilot.retrieval_core import _CORPUS_STORE_CACHE

    monkeypatch.setattr(ingestion_module, "INGESTED_DOCS_DIR", tmp_path)
    (tmp_path / "FAKE.json").write_text(
        json.dumps([{"id": "FAKE-001", "title": "Fake", "category": "ingested", "text": "fake ingested text"}])
    )
    _CORPUS_STORE_CACHE.pop((_PINNED_CORPUS_NAME, True), None)
    try:
        report = evaluate(mock=True)
        assert report["corpus_size"] == 15  # not 16 — the ingested doc must not appear
    finally:
        _CORPUS_STORE_CACHE.pop((_PINNED_CORPUS_NAME, True), None)

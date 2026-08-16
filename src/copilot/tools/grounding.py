"""
Grounding tool — RAG retrieval over the merchant-policy/onboarding corpus.

Reuses src.copilot.retrieval_core's generic vector store/embedder/context-
budget machinery (extracted from src/parte4_api/retrieval.py — see
DECISIONS.md D22) for a second, independent corpus (data/policy_docs.json)
instead of a duplicate retrieval implementation. Same anti-overengineering
reasoning as DECISIONS.md D17-D19: ~15 records, brute-force cosine search is
still microseconds — no vector DB warranted.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.copilot.retrieval_core import dedupe_by_field, fit_to_budget, get_corpus_store

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "data"
POLICY_DOCS_PATH = DATA_DIR / "policy_docs.json"

CORPUS_NAME = "policy_docs"
# Same budget convention as retrieval.py's historical-complaints corpus (D20).
DEFAULT_MAX_CONTEXT_CHARS = 800


def _load_policy_docs() -> list[dict[str, Any]]:
    if not POLICY_DOCS_PATH.exists():
        return []
    return json.loads(POLICY_DOCS_PATH.read_text())


def known_policy_ids() -> set[str]:
    """All ids in the corpus — used by the eval harness's citation-
    hallucination check (a cited id must exist here) without needing a full
    retrieval call."""
    return {d["id"] for d in _load_policy_docs()}


def retrieve_policy(
    query_text: str,
    k: int = 3,
    mock: bool = True,
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
) -> list[dict[str, Any]]:
    """Retrieves the top-k policy docs most similar to `query_text`. Mirrors
    src/parte4_api/retrieval.py:retrieve_similar_cases' shape (over-fetch,
    dedupe, budget) but for the policy corpus — each result keeps its
    id/title/category/text so the orchestrator can cite it directly.
    """
    store, embedder = get_corpus_store(CORPUS_NAME, _load_policy_docs, text_field="text", mock=mock)
    if len(store) == 0:
        return []
    query_vec = embedder.embed([query_text])[0]
    # Over-fetch 2k, same reasoning as retrieve_similar_cases: dedup can
    # remove results, and we still want up to k distinct docs back.
    raw_results = store.query(query_vec, k=k * 2)
    docs = dedupe_by_field(raw_results, field="text")[:k]
    return fit_to_budget(docs, max_context_chars, field="text")

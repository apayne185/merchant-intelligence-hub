"""
Retrieval-augmented context for the complaint classifier.

Embeds `data/historical_complaints.json` (synthetic past cases) and retrieves
the top-k most similar ones to ground classification in how similar
complaints were actually handled before. See DECISIONS.md "Parte 4b · RAG
retrieval" for the design rationale (why a brute-force in-repo vector store
instead of a real vector DB, why TF-IDF stands in for embeddings in mock mode).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.copilot.retrieval_core import Embedder, SimpleVectorStore, dedupe_by_field, fit_to_budget
from src.copilot.retrieval_core import MockEmbedder as _MockEmbedder
from src.copilot.retrieval_core import OpenAIEmbedder as _OpenAIEmbedder

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
HISTORICAL_COMPLAINTS_PATH = DATA_DIR / "historical_complaints.json"

# SimpleVectorStore, Embedder, and the mock/real embedders now live in
# src/copilot/retrieval_core.py — extracted so the Grounding tool's second
# corpus (data/policy_docs.json) can share this mechanism instead of a
# duplicate copy. See DECISIONS.md D22. Everything below (caching,
# dedup/budget wrappers, retrieve_similar_cases) is this module's own
# corpus-specific logic and is unchanged.


# -----------------------------------------------------------------------------
# Case store — lazily built and cached per mock/real mode, so embedding the
# corpus happens at most once per mode per process, not once per request.
# Keyed by mode (not a single global) because tests exercise both modes in
# the same pytest process.
# -----------------------------------------------------------------------------
def _load_historical_complaints() -> list[dict[str, Any]]:
    if not HISTORICAL_COMPLAINTS_PATH.exists():
        return []
    return json.loads(HISTORICAL_COMPLAINTS_PATH.read_text())


def build_case_store(mock: bool) -> tuple[SimpleVectorStore, Embedder]:
    """Loads the historical complaints corpus, embeds it, and returns a
    populated store + the embedder used (queries must reuse it — a query
    embedded with a different vectorizer/model wouldn't share the same
    vector space as the corpus).
    """
    records = _load_historical_complaints()
    texts = [r["email_text"] for r in records]

    embedder: Embedder = _MockEmbedder(texts) if mock else _OpenAIEmbedder()

    store = SimpleVectorStore()
    if texts:
        vectors = embedder.embed(texts)
        store.add(records, vectors)
    return store, embedder


_CASE_STORE_CACHE: dict[bool, tuple[SimpleVectorStore, Embedder]] = {}


def get_case_store(mock: bool) -> tuple[SimpleVectorStore, Embedder]:
    if mock not in _CASE_STORE_CACHE:
        _CASE_STORE_CACHE[mock] = build_case_store(mock=mock)
    return _CASE_STORE_CACHE[mock]


# -----------------------------------------------------------------------------
# Context-window management: retrieval doesn't just rank and return — it also
# has to fit whatever it returns into the LLM's context alongside everything
# else in the prompt. Two concrete concerns, both real production issues:
#   1. Near-duplicate cases waste budget without adding signal.
#   2. Unbounded resolution_notes length could blow past a token budget if
#      the corpus ever included longer free-text notes than today's.
# -----------------------------------------------------------------------------
DEFAULT_MAX_CONTEXT_CHARS = 800  # total budget across all k cases' resolution_notes


def _dedupe_by_resolution(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drops cases with an identical resolution_notes text. Near-duplicate
    retrieved cases (same underlying incident logged twice, or two cases
    resolved the same way) waste context budget without adding signal.

    Thin wrapper over the generic src.copilot.retrieval_core.dedupe_by_field
    (D22) fixed to this corpus' text field.
    """
    return dedupe_by_field(records, field="resolution_notes")


def _fit_to_budget(records: list[dict[str, Any]], max_chars: int) -> list[dict[str, Any]]:
    """Truncates resolution_notes so the total injected context stays under
    a fixed character budget, dropping lower-ranked cases entirely once the
    budget runs out. A character budget is a coarse stand-in for real token
    accounting (see DECISIONS.md) — good enough given resolution_notes are
    short, plain-text, single-language-family strings.

    Thin wrapper over the generic src.copilot.retrieval_core.fit_to_budget
    (D22) fixed to this corpus' text field.
    """
    return fit_to_budget(records, max_chars, field="resolution_notes")


def retrieve_similar_cases(
    email_text: str,
    k: int = 3,
    mock: bool = True,
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
) -> list[dict[str, Any]]:
    """Retrieves the top-k historical complaints most similar to `email_text`.

    Returns each case's category/urgency/resolution_notes (synthetic data,
    no PII) so the agent can ground its classification in how similar past
    cases were actually handled, instead of classifying cold every time.
    Deduplicates near-identical cases and enforces `max_context_chars` across
    the returned notes — see the context-window-management block above.
    """
    store, embedder = get_case_store(mock=mock)
    if len(store) == 0:
        return []
    query_vec = embedder.embed([email_text])[0]
    # Over-fetch 2k: dedup can remove results, and we still want up to k
    # distinct cases back afterward rather than silently returning fewer.
    raw_results = store.query(query_vec, k=k * 2)
    cases = [
        {
            "category": r["category"],
            "urgency": r["urgency"],
            "resolution_notes": r["resolution_notes"],
        }
        for r in raw_results
    ]
    cases = _dedupe_by_resolution(cases)[:k]
    return _fit_to_budget(cases, max_context_chars)

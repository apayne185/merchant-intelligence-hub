"""
Corpus-agnostic RAG machinery: in-memory vector store, mock/real embedders,
and generic context-window management (dedup + character budget).

Extracted from src/parte4_api/retrieval.py (DECISIONS.md D17-D20), which
built this for a single corpus (historical complaints). A second corpus
(data/policy_docs.json, the Grounding tool in src/copilot/tools/grounding.py)
needed the exact same mechanics, and duplicating a working, already-tested
vector store instead of sharing it would be the kind of unjustified
reinvention this project's own decisions (D17-D19) argue against — see
DECISIONS.md D22.

src/parte4_api/retrieval.py imports SimpleVectorStore and the embedders from
here and keeps its own corpus-specific caching/wrapper functions
(build_case_store, get_case_store, retrieve_similar_cases) — its public API
is unchanged by this extraction.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

import numpy as np


# -----------------------------------------------------------------------------
# Vector store — brute-force cosine similarity, adequate for ~10s-100s of
# records held in memory. Not meant to scale past that without swapping in a
# real ANN index (FAISS/pgvector/Pinecone) — see DECISIONS.md D17.
# -----------------------------------------------------------------------------
def _cosine_similarity(query_vec: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    query_vec = query_vec.reshape(1, -1)
    query_norm = np.linalg.norm(query_vec, axis=1, keepdims=True)
    matrix_norm = np.linalg.norm(matrix, axis=1, keepdims=True)
    query_norm = np.where(query_norm == 0, 1e-9, query_norm)
    matrix_norm = np.where(matrix_norm == 0, 1e-9, matrix_norm)
    sims = (matrix @ query_vec.T) / (matrix_norm * query_norm.T)
    return sims.ravel()


class SimpleVectorStore:
    """Minimal in-memory vector store: add() + query() by cosine similarity."""

    def __init__(self) -> None:
        self._vectors: np.ndarray | None = None
        self._records: list[dict[str, Any]] = []

    def __len__(self) -> int:
        return len(self._records)

    def add(self, records: list[dict[str, Any]], vectors: np.ndarray) -> None:
        self._records.extend(records)
        self._vectors = vectors if self._vectors is None else np.vstack([self._vectors, vectors])

    def query(self, vector: np.ndarray, k: int = 3) -> list[dict[str, Any]]:
        # k <= 0 guarded explicitly: a negative k reaching the slice below
        # would hit Python's negative-slice semantics ([:-1] drops the last
        # element instead of returning nothing) and silently return almost
        # the whole corpus instead of an empty result.
        if not self._records or self._vectors is None or k <= 0:
            return []
        sims = _cosine_similarity(vector, self._vectors)
        k = min(k, len(self._records))
        # lexsort with a fixed secondary key (original index), not argsort+
        # reverse: numpy's default argsort isn't stable, and even a stable
        # ascending sort reversed via [::-1] flips tie order too — same
        # nondeterminism class already fixed once in parte3_modeling.ipynb's
        # recall_at_k. With ties (two corpus entries equally similar to the
        # query), this keeps results reproducible run-to-run instead of
        # depending on numpy's internal tie-breaking.
        order = np.lexsort((np.arange(len(sims)), -sims))
        top_idx = order[:k]
        return [self._records[i] for i in top_idx]


# -----------------------------------------------------------------------------
# Embedders — real (OpenAI) vs. mock (offline, deterministic, no network call
# or model download). Every corpus using this module follows the same
# MOCK_LLM=1-must-work-at-zero-cost rule as the rest of the repo.
# -----------------------------------------------------------------------------
class Embedder(Protocol):
    def embed(self, texts: list[str]) -> np.ndarray: ...


class MockEmbedder:
    """Deterministic, offline stand-in for real embeddings.

    TF-IDF over the corpus text, not a semantic embedding — it ranks lexical
    overlap, not meaning. Good enough to demonstrate the retrieval mechanics
    without a model download; real semantic similarity requires OpenAIEmbedder.
    """

    def __init__(self, corpus_texts: list[str]) -> None:
        from sklearn.feature_extraction.text import TfidfVectorizer

        self._vectorizer = TfidfVectorizer(max_features=256)
        if corpus_texts:
            self._vectorizer.fit(corpus_texts)

    def embed(self, texts: list[str]) -> np.ndarray:
        return self._vectorizer.transform(texts).toarray()


class OpenAIEmbedder:
    """Real embeddings via OpenAI's `text-embedding-3-small`."""

    def __init__(self, model: str = "text-embedding-3-small") -> None:
        from openai import OpenAI

        self._client = OpenAI()
        self._model = model

    def embed(self, texts: list[str]) -> np.ndarray:
        response = self._client.embeddings.create(model=self._model, input=texts)
        return np.array([item.embedding for item in response.data])


# -----------------------------------------------------------------------------
# Generic context-window management — see DECISIONS.md D20 for the original
# rationale (near-duplicate cases waste context budget; unbounded text could
# blow past a token budget). Parameterized by field name so any corpus'
# records (resolution_notes, policy text, ...) can reuse the same logic.
# -----------------------------------------------------------------------------
def dedupe_by_field(records: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    """Drops records with an identical (case/whitespace-insensitive) value
    in `field`. Generalizes retrieval.py's original _dedupe_by_resolution."""
    seen: set[str] = set()
    deduped = []
    for r in records:
        key = r[field].strip().lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    return deduped


def fit_to_budget(records: list[dict[str, Any]], max_chars: int, field: str) -> list[dict[str, Any]]:
    """Truncates `field` so the total injected context stays under a fixed
    character budget, dropping lower-ranked records entirely once the budget
    runs out. Generalizes retrieval.py's original _fit_to_budget."""
    budget = max_chars
    fitted = []
    for r in records:
        if budget <= 0:
            break
        text = r[field]
        if len(text) > budget:
            text = text[: max(0, budget - 1)].rstrip() + "…"
        fitted.append({**r, field: text})
        budget -= len(text)
    return fitted


# -----------------------------------------------------------------------------
# Multi-corpus store cache — generalizes retrieval.py's get_case_store/
# build_case_store (D19) from a single corpus keyed by mode, to any number
# of named corpora each keyed by (corpus_name, mode). Same reasoning as D19:
# tests exercise multiple corpora and both modes in one pytest process, so a
# single global store would return the wrong one.
# -----------------------------------------------------------------------------
CorpusLoader = Callable[[], list[dict[str, Any]]]

_CORPUS_STORE_CACHE: dict[tuple[str, bool], tuple[SimpleVectorStore, Embedder]] = {}


def get_corpus_store(
    corpus_name: str, loader: CorpusLoader, text_field: str, mock: bool
) -> tuple[SimpleVectorStore, Embedder]:
    """Loads+embeds `corpus_name` on first use per (corpus_name, mock), then
    returns the cached store + the embedder used (queries must reuse it — an
    embedding from a different vectorizer/model wouldn't share the corpus'
    vector space)."""
    key = (corpus_name, mock)
    if key not in _CORPUS_STORE_CACHE:
        records = loader()
        texts = [r[text_field] for r in records]
        embedder: Embedder = MockEmbedder(texts) if mock else OpenAIEmbedder()
        store = SimpleVectorStore()
        if texts:
            store.add(records, embedder.embed(texts))
        _CORPUS_STORE_CACHE[key] = (store, embedder)
    return _CORPUS_STORE_CACHE[key]

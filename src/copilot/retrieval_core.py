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

import os
from collections.abc import Callable
from typing import Any, Protocol

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

# AZURE_OPENAI_ENDPOINT is the one env var Microsoft's SDK docs treat as
# "Azure is configured" — api_key/api_version have same-named non-Azure
# fallbacks (AZURE_OPENAI_API_KEY isn't the same var as OPENAI_API_KEY, but
# both mean "some OpenAI-compatible API key exists"), while an Azure
# resource endpoint URL has no ambiguous non-Azure meaning. So it's what
# _select_real_embedder() below branches on. See DECISIONS.md D39.
_AZURE_ENDPOINT_ENV_VAR = "AZURE_OPENAI_ENDPOINT"


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

    @property
    def records(self) -> list[dict[str, Any]]:
        """The exact records this store was built from — lets callers (e.g.
        an eval harness's hallucination check) inspect what's actually
        cached and being served, instead of re-reading the source file and
        risking a stale/live desync once the store is cached. See
        DECISIONS.md D34/D35."""
        return list(self._records)

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


class AzureOpenAIEmbedder:
    """Real embeddings via an Azure OpenAI resource — same
    embeddings.create() call shape as OpenAIEmbedder, but against an
    Azure-hosted deployment instead of api.openai.com. See DECISIONS.md
    D39 for why this is a third branch alongside Mock/OpenAI rather than a
    replacement for either.

    `model` here is the Azure **deployment name**, not the underlying
    model id (e.g. "text-embedding-3-small") — Azure OpenAI resources
    route by deployment, a resource-specific name an admin chose when
    creating the deployment, which may or may not match the model id
    itself. Defaults to AZURE_OPENAI_EMBEDDING_DEPLOYMENT so this can be
    swapped without a code change if a deployment gets renamed.
    """

    def __init__(self, model: str | None = None) -> None:
        from openai import AzureOpenAI

        # AzureOpenAI() with no args already reads AZURE_OPENAI_API_KEY,
        # AZURE_OPENAI_ENDPOINT, and OPENAI_API_VERSION from the
        # environment (see the openai SDK's own docstring) — no need to
        # thread them through here ourselves, same as OpenAIEmbedder
        # leaning on OpenAI()'s OPENAI_API_KEY auto-read above.
        self._client = AzureOpenAI()
        self._model = model or os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")

    def embed(self, texts: list[str]) -> np.ndarray:
        response = self._client.embeddings.create(model=self._model, input=texts)
        return np.array([item.embedding for item in response.data])


def _select_real_embedder() -> Embedder:
    """Picks the real (non-mock) embedder backend. AZURE_OPENAI_ENDPOINT
    present means an Azure resource is actually configured for this
    process — route there; otherwise fall back to plain OpenAI, unchanged
    from before this function existed. Deliberately NOT gated by a
    separate "which backend" flag: the presence of Azure-specific
    configuration is itself the signal, so a deployment that sets
    AZURE_OPENAI_ENDPOINT gets Azure without needing a second env var to
    also flip, and a deployment that never sets it keeps working exactly
    as before with zero config changes. See DECISIONS.md D39.
    """
    if os.environ.get(_AZURE_ENDPOINT_ENV_VAR):
        return AzureOpenAIEmbedder()
    return OpenAIEmbedder()


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
        embedder: Embedder = MockEmbedder(texts) if mock else _select_real_embedder()
        store = SimpleVectorStore()
        if texts:
            store.add(records, embedder.embed(texts))
        _CORPUS_STORE_CACHE[key] = (store, embedder)
    return _CORPUS_STORE_CACHE[key]

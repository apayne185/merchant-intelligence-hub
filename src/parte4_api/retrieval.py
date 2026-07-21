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
from typing import Any, Protocol

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
HISTORICAL_COMPLAINTS_PATH = DATA_DIR / "historical_complaints.json"


# -----------------------------------------------------------------------------
# Vector store — brute-force cosine similarity, adequate for ~10s-100s of
# records held in memory. Not meant to scale past that without swapping in a
# real ANN index (FAISS/pgvector/Pinecone) — see DECISIONS.md.
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
# or model download). Same mock/real split as `_MockAgent`/`_RealAgentAdapter`
# in agent.py, for the same reason: MOCK_LLM=1 must work with zero cost and
# zero external dependencies.
# -----------------------------------------------------------------------------
class Embedder(Protocol):
    def embed(self, texts: list[str]) -> np.ndarray: ...


class _MockEmbedder:
    """Deterministic, offline stand-in for real embeddings.

    TF-IDF over the corpus text, not a semantic embedding — it ranks lexical
    overlap, not meaning. Good enough to demonstrate the retrieval mechanics
    without a model download; real semantic similarity requires the OpenAI
    embedder below.
    """

    def __init__(self, corpus_texts: list[str]) -> None:
        from sklearn.feature_extraction.text import TfidfVectorizer

        self._vectorizer = TfidfVectorizer(max_features=256)
        if corpus_texts:
            self._vectorizer.fit(corpus_texts)

    def embed(self, texts: list[str]) -> np.ndarray:
        return self._vectorizer.transform(texts).toarray()


class _OpenAIEmbedder:
    """Real embeddings via OpenAI's `text-embedding-3-small`."""

    def __init__(self, model: str = "text-embedding-3-small") -> None:
        from openai import OpenAI

        self._client = OpenAI()
        self._model = model

    def embed(self, texts: list[str]) -> np.ndarray:
        response = self._client.embeddings.create(model=self._model, input=texts)
        return np.array([item.embedding for item in response.data])


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
    """
    seen: set[str] = set()
    deduped = []
    for r in records:
        key = r["resolution_notes"].strip().lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    return deduped


def _fit_to_budget(records: list[dict[str, Any]], max_chars: int) -> list[dict[str, Any]]:
    """Truncates resolution_notes so the total injected context stays under
    a fixed character budget, dropping lower-ranked cases entirely once the
    budget runs out. A character budget is a coarse stand-in for real token
    accounting (see DECISIONS.md) — good enough given resolution_notes are
    short, plain-text, single-language-family strings.
    """
    budget = max_chars
    fitted = []
    for r in records:
        if budget <= 0:
            break
        notes = r["resolution_notes"]
        if len(notes) > budget:
            notes = notes[: max(0, budget - 1)].rstrip() + "…"
        fitted.append({**r, "resolution_notes": notes})
        budget -= len(notes)
    return fitted


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

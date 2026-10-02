"""
Corpus-agnostic retrieval: BM25 lexical search, an in-memory dense vector
store, and hybrid ranking with Reciprocal Rank Fusion.

  mock mode: BM25 only (offline, deterministic, no model download);
  real mode: BM25 and dense embeddings, fused with RRF (k=60).

Why hybrid for filings: dense embeddings capture paraphrase ("dependence on
our CEO" ~ "key personnel"), while BM25 keeps rare exact terms that decide
relevance in legal text ("talc", "Section 232", "CHIPS Act") from being
averaged away. RRF combines rank positions, so the two scores never need to
be calibrated against each other.

Scale note: brute-force cosine over ~1k passages is a single matrix-vector
product (well under a millisecond), so an ANN index (FAISS, pgvector) would
add an operational dependency without a measurable latency win at this
corpus size. The store's interface (add/query) is what an ANN-backed
replacement would implement.
"""
from __future__ import annotations

import os
import re
import threading
from collections.abc import Callable
from typing import Any, Protocol

import numpy as np

_AZURE_ENDPOINT_ENV_VAR = "AZURE_OPENAI_ENDPOINT"
_EMBED_BATCH = 256


class SimpleVectorStore:
    """Rows are L2-normalised once at insert time, so a query is one dot product."""

    def __init__(self) -> None:
        self._vectors: np.ndarray | None = None
        self._records: list[dict[str, Any]] = []

    def __len__(self) -> int:
        return len(self._records)

    @property
    def records(self) -> list[dict[str, Any]]:
        return list(self._records)

    @staticmethod
    def _normalise(m: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(m, axis=-1, keepdims=True)
        return m / np.where(norms == 0, 1.0, norms)

    def add(self, records: list[dict[str, Any]], vectors: np.ndarray) -> None:
        vectors = self._normalise(np.asarray(vectors, dtype=np.float64))
        self._records.extend(records)
        self._vectors = vectors if self._vectors is None else np.vstack([self._vectors, vectors])

    def query(self, vector: np.ndarray, k: int = 3) -> list[dict[str, Any]]:
        if not self._records or self._vectors is None or k <= 0:
            return []
        sims = self._vectors @ self._normalise(np.asarray(vector, dtype=np.float64))
        k = min(k, len(self._records))
        # Stable tie-break on insertion order: identical scores must not make
        # results (and therefore citations) vary between runs.
        order = np.lexsort((np.arange(len(sims)), -sims))
        return [self._records[i] for i in order[:k]]


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> np.ndarray: ...


# Ordered longest-first; "-ion" alone is not stripped ("mention", "region"),
# only "-ation(s)", so regulation/regulations/regulatory all become "regulat".
_SUFFIXES = ("ations", "ation", "ories", "ory", "ing", "ies", "es", "ed", "ly", "s")
# Question scaffolding that carries no topical signal but can have high IDF.
_QUERY_WORDS = frozenset({"does", "did", "say", "says", "said", "mention", "mentions", "disclose", "discloses",
                          "describe", "describes", "tell", "company", "companies", "10", "k"})
_ENGLISH_STOP = frozenset("""
a about above after again against all also am an and any are as at be because been before being below
between both but by can could did do does doing down during each few for from further had has have
having he her here hers herself him himself his how however i if in into is it its itself just may me
might more most must my myself no nor not now of off on once only or other our ours ourselves out over
own same shall she should so some such than that the their theirs them themselves then there these they
this those through to too under until up upon us very was we were what when where which while who whom
why will with within without would you your yours yourself yourselves
""".split())
_STOP = _ENGLISH_STOP | _QUERY_WORDS
_TOKEN = re.compile(r"[a-z0-9]+")


def analyze(text: str) -> list[str]:
    """Lowercase word tokens, English stop words removed, light suffix
    stripping so regulation/regulations/regulatory share one term."""
    out = []
    for tok in _TOKEN.findall(text.lower()):
        if tok in _STOP or len(tok) < 2:
            continue
        for suf in _SUFFIXES:
            if tok.endswith(suf) and len(tok) - len(suf) >= 4:
                tok = tok[: -len(suf)]
                break
        out.append(tok)
    return out


class BM25Index:
    """Okapi BM25 (k1=1.5, b=0.75) over postings lists: for each term, the
    documents containing it and its frequency in each. Scoring a query only
    touches the postings of its own terms."""

    def __init__(self, texts: list[str], k1: float = 1.5, b: float = 0.75) -> None:
        self._n = len(texts)
        postings: dict[str, dict[int, int]] = {}
        lengths = np.zeros(self._n)
        for doc, text in enumerate(texts):
            terms = analyze(text)
            lengths[doc] = len(terms)
            for term in terms:
                bucket = postings.setdefault(term, {})
                bucket[doc] = bucket.get(doc, 0) + 1
        self._postings = {
            t: (np.fromiter(d.keys(), dtype=np.int64), np.fromiter(d.values(), dtype=np.float64))
            for t, d in postings.items()
        }
        avgdl = float(lengths.mean()) if self._n else 1.0
        self._norm = k1 * (1 - b + b * lengths / (avgdl or 1.0))
        self._k1 = k1

    def idf(self, term: str) -> float:
        df = len(self._postings[term][0]) if term in self._postings else 0
        return float(np.log(1.0 + (self._n - df + 0.5) / (df + 0.5)))

    def scores(self, query: str) -> np.ndarray:
        out = np.zeros(self._n)
        for term in set(analyze(query)):
            if term not in self._postings:
                continue
            docs, tf = self._postings[term]
            out[docs] += self.idf(term) * tf * (self._k1 + 1) / (tf + self._norm[docs])
        return out


def _ranked(scores: np.ndarray, k: int) -> list[int]:
    """Top-k indices with a stable tie-break on insertion order."""
    order = np.lexsort((np.arange(len(scores)), -scores))
    return [int(i) for i in order[:k] if scores[i] > 0]


def reciprocal_rank_fusion(rankings: list[list[int]], k: int = 60) -> list[int]:
    fused: dict[int, float] = {}
    for ranking in rankings:
        for rank, idx in enumerate(ranking):
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (k + rank + 1)
    return [i for i, _ in sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))]


class OpenAIEmbedder:
    def __init__(self, model: str = "text-embedding-3-small") -> None:
        from openai import OpenAI

        self._client = OpenAI()
        self._model = model

    def embed(self, texts: list[str]) -> np.ndarray:
        out: list[list[float]] = []
        for i in range(0, len(texts), _EMBED_BATCH):
            resp = self._client.embeddings.create(model=self._model, input=texts[i : i + _EMBED_BATCH])
            out.extend(item.embedding for item in resp.data)
        return np.array(out)


class AzureOpenAIEmbedder(OpenAIEmbedder):
    """Same call shape against an Azure OpenAI deployment. `model` is the
    Azure deployment name (AZURE_OPENAI_EMBEDDING_DEPLOYMENT), not the model id."""

    def __init__(self, model: str | None = None) -> None:
        from openai import AzureOpenAI

        self._client = AzureOpenAI()
        self._model = model or os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")


def _select_real_embedder() -> Embedder:
    """An Azure endpoint in the environment means Azure is the configured backend."""
    if os.environ.get(_AZURE_ENDPOINT_ENV_VAR):
        return AzureOpenAIEmbedder()
    return OpenAIEmbedder()


def dedupe_by_field(records: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    """Drops records whose `field` is identical after case/whitespace folding
    (10-Ks repeat boilerplate paragraphs; repeats waste context budget)."""
    seen: set[str] = set()
    out = []
    for r in records:
        key = " ".join(r[field].lower().split())
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


class HybridIndex:
    """BM25 alone (embedder=None) or BM25 + dense, fused with RRF."""

    def __init__(self, records: list[dict[str, Any]], text_field: str, embedder: Embedder | None) -> None:
        self.records = records
        texts = [r[text_field] for r in records]
        self._bm25 = BM25Index(texts)
        self._embedder = embedder
        self._dense: SimpleVectorStore | None = None
        if embedder is not None and texts:
            self._dense = SimpleVectorStore()
            self._dense.add([{"i": i} for i in range(len(texts))], embedder.embed(texts))

    def __len__(self) -> int:
        return len(self.records)

    def search(self, query: str, k: int) -> list[dict[str, Any]]:
        if not self.records or k <= 0:
            return []
        depth = max(k * 4, 20)
        rankings = [_ranked(self._bm25.scores(query), depth)]
        if self._dense is not None and self._embedder is not None:
            hits = self._dense.query(self._embedder.embed([query])[0], k=depth)
            rankings.append([h["i"] for h in hits])
        return [self.records[i] for i in reciprocal_rank_fusion(rankings)[:k]]


CorpusLoader = Callable[[], list[dict[str, Any]]]
_CACHE: dict[tuple[str, bool], HybridIndex] = {}
_CACHE_LOCK = threading.Lock()


def get_index(corpus_name: str, loader: CorpusLoader, text_field: str, mock: bool) -> HybridIndex:
    """Builds the index for (corpus_name, mock) once per process."""
    key = (corpus_name, mock)
    with _CACHE_LOCK:
        if key not in _CACHE:
            _CACHE[key] = HybridIndex(loader(), text_field, None if mock else _select_real_embedder())
        return _CACHE[key]

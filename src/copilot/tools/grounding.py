"""
Grounding tool — RAG retrieval over the merchant-policy/onboarding corpus.

Reuses src.copilot.retrieval_core's generic vector store/embedder/context-
budget machinery (extracted from src/parte4_api/retrieval.py — see
DECISIONS.md D22) for a second, independent corpus (data/policy_docs.json)
instead of a duplicate retrieval implementation. Same anti-overengineering
reasoning as DECISIONS.md D17-D19: ~15 records, brute-force cosine search is
still microseconds — no vector DB warranted.

The corpus this tool searches is policy_docs.json PLUS any PDFs run through
src.copilot.tools.ingestion.ingest_and_index() (data/ingested_docs/*.json)
— one merged corpus, not two separately-queried ones stitched together
after the fact. ingest_and_index() deliberately produces records in this
exact {id, title, category, text} shape so a scanned/ingested policy PDF
and a hand-written policy_docs.json entry are indistinguishable to
retrieval — a question doesn't need to know or care which pipeline
produced the document it cites. See DECISIONS.md D38.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.copilot.retrieval_core import dedupe_by_field, fit_to_budget, get_corpus_store
from src.copilot.tools import ingestion as ingestion_module

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "data"
POLICY_DOCS_PATH = DATA_DIR / "policy_docs.json"

CORPUS_NAME = "policy_docs"
# Same budget convention as retrieval.py's historical-complaints corpus (D20).
DEFAULT_MAX_CONTEXT_CHARS = 800


_REQUIRED_RECORD_FIELDS = ("id", "title", "category", "text")


def _validate_records(records: list[dict[str, Any]], source: str) -> None:
    """Fails loudly, naming the offending source file, instead of letting
    a malformed record reach retrieval_core.dedupe_by_field/fit_to_budget
    and raise an opaque bare KeyError/AttributeError from deep inside
    generic helpers that have no idea which corpus file it came from. This
    matters specifically because the corpus is now a *merge* of a
    hand-written file and machine-generated ones (D38) — a hand-edited
    policy_docs.json missing a field, or an ingested file from an
    older/newer schema, used to take down /ask with no indication of
    which record or file was at fault.
    """
    for i, record in enumerate(records):
        missing = [f for f in _REQUIRED_RECORD_FIELDS if f not in record]
        if missing:
            raise ValueError(f"{source}: record {i} (id={record.get('id')!r}) missing field(s) {missing}")
        if not isinstance(record["text"], str):
            raise ValueError(f"{source}: record {i} (id={record['id']!r}) has non-string 'text': {type(record['text'])}")


def _load_policy_docs() -> list[dict[str, Any]]:
    """Reads ingestion_module.INGESTED_DOCS_DIR (not a `from ... import
    INGESTED_DOCS_DIR` bound name) specifically so tests can monkeypatch
    the ingestion module's attribute and have this function see it — a
    bare-name import would freeze the path at this module's own import
    time, making that monkeypatch a silent no-op.

    Validates every record's shape (see _validate_records) and rejects a
    duplicate id across sources — without the latter, an ingested doc
    could silently shadow a curated policy_docs.json entry sharing its id:
    both would live in the vector store, known_policy_ids() would collapse
    the duplicate into one (making the eval harness's hallucination check
    blind to it), and a citation pointing at that id would be ambiguous
    about which document actually grounded the answer. For a system whose
    whole value proposition is *cited* answers, a silently ambiguous
    citation id is a real integrity gap, not a cosmetic one.
    """
    records: list[dict[str, Any]] = []
    seen_ids: dict[str, str] = {}  # id -> source file it first appeared in

    if POLICY_DOCS_PATH.exists():
        policy_records = json.loads(POLICY_DOCS_PATH.read_text())
        _validate_records(policy_records, str(POLICY_DOCS_PATH))
        for r in policy_records:
            seen_ids[r["id"]] = str(POLICY_DOCS_PATH)
        records.extend(policy_records)

    ingested_dir = ingestion_module.INGESTED_DOCS_DIR
    if ingested_dir.exists():
        for path in sorted(ingested_dir.glob("*.json")):
            ingested_records = json.loads(path.read_text())
            _validate_records(ingested_records, str(path))
            for r in ingested_records:
                if r["id"] in seen_ids:
                    raise ValueError(
                        f"duplicate id {r['id']!r} in {path} — already defined in {seen_ids[r['id']]}"
                    )
                seen_ids[r["id"]] = str(path)
            records.extend(ingested_records)

    return records


def known_policy_ids(mock: bool = True) -> set[str]:
    """All ids in the corpus actually being served — used by the eval
    harness's citation-hallucination check (a cited id must exist here)
    without needing a full retrieval call.

    Reads from the same cached corpus store retrieve_policy() serves from
    (get_corpus_store(..., mock=mock)), not a fresh re-read of
    policy_docs.json — the store is cached for the process lifetime on
    first use, so re-reading the file directly could desync from what's
    actually served if the file changed after that first call. See
    DECISIONS.md D34/D35.
    """
    store, _ = get_corpus_store(CORPUS_NAME, _load_policy_docs, text_field="text", mock=mock)
    return {d["id"] for d in store.records}


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

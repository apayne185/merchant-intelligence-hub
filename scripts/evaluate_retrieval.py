"""
Retrieval-quality benchmark for the Grounding tool's vector store
(src/copilot/retrieval_core.py) — recall@k and MRR, mock (TF-IDF) vs. real
(OpenAI embeddings), against data/policy_docs.json.

This answers a specific gap the other eval harnesses don't cover:
evaluate_copilot.py checks whether the *right policy doc* got cited for a
handful of end-to-end questions, but never measures retrieval quality in
isolation, and never compares the two embedders against each other. A
weak retriever can hide behind a strong synthesizer in the end-to-end eval
(the LLM can sometimes patch over a mediocre top-k) — this benchmark
isolates retrieval so a regression there shows up here first.

Ground truth: each policy doc's own `category` field (data/policy_docs.json
already has 15 docs across 6 categories — onboarding, risk, escalation,
billing, compliance) plus a small hand-written query set
(data/golden_set_retrieval.json) mapping a natural-language query to the
doc id(s) that should be retrieved. Category is used for a cheap, larger-N
sanity metric (precision@k against same-category docs); the hand-labeled
queries are used for the real metric (recall@k / MRR against a specific
expected doc), same "small but actually-checkable" philosophy as D21/D28
rather than a synthetic large-N benchmark this repo can't actually
validate by hand.

Usage:
    MOCK_LLM=1 uv run python -m scripts.evaluate_retrieval
    OPENAI_API_KEY=sk-... uv run python -m scripts.evaluate_retrieval --real
    uv run python -m scripts.evaluate_retrieval --both   # mock AND real, side by side (needs OPENAI_API_KEY)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_SET_PATH = REPO_ROOT / "data" / "golden_set_retrieval.json"
OUTPUTS_DIR = REPO_ROOT / "outputs"

K_VALUES = [1, 3, 5]


def _load_golden_set() -> list[dict[str, Any]]:
    return json.loads(GOLDEN_SET_PATH.read_text())


def _reciprocal_rank(ranked_ids: list[str], expected_ids: set[str]) -> float:
    for rank, doc_id in enumerate(ranked_ids, start=1):
        if doc_id in expected_ids:
            return 1.0 / rank
    return 0.0


def _recall_at_k(ranked_ids: list[str], expected_ids: set[str], k: int) -> float:
    if not expected_ids:
        return 1.0
    hit = set(ranked_ids[:k]) & expected_ids
    return len(hit) / len(expected_ids)


def evaluate(mock: bool) -> dict[str, Any]:
    """Runs the golden query set against the policy corpus and reports
    recall@k / MRR / same-category precision@k for one embedder.

    Imports are deferred (not module-level) because OpenAIEmbedder's
    constructor (retrieval_core.py) instantiates an OpenAI client at
    __init__ time — importing get_corpus_store eagerly at module scope
    would be fine either way here, but keeping the import inside the
    function matches evaluate_copilot.py's own MOCK_LLM-branch-at-call-time
    pattern (see its docstring) so both harnesses read the same at a glance.
    """
    from src.copilot.retrieval_core import get_corpus_store
    from src.copilot.tools.grounding import CORPUS_NAME, _load_policy_docs

    # Force a fresh, unmocked-corpus-cache read per mode: get_corpus_store
    # caches by (corpus_name, mock), so calling this twice in one process
    # (as --both does) correctly reuses each mode's own cache rather than
    # cross-contaminating mock/real results.
    store, embedder = get_corpus_store(CORPUS_NAME, _load_policy_docs, text_field="text", mock=mock)
    docs_by_id = {d["id"]: d for d in store.records}

    golden_set = _load_golden_set()
    per_query: list[dict[str, Any]] = []
    recall_sums = dict.fromkeys(K_VALUES, 0.0)
    category_precision_sums = dict.fromkeys(K_VALUES, 0.0)
    mrr_sum = 0.0

    for example in golden_set:
        query_vec = embedder.embed([example["query"]])[0]
        ranked = store.query(query_vec, k=len(docs_by_id))
        ranked_ids = [d["id"] for d in ranked]
        expected_ids = set(example["expected_doc_ids"])
        expected_category = docs_by_id[example["expected_doc_ids"][0]]["category"]

        rr = _reciprocal_rank(ranked_ids, expected_ids)
        mrr_sum += rr

        recalls = {}
        cat_precisions = {}
        for k in K_VALUES:
            r = _recall_at_k(ranked_ids, expected_ids, k)
            recalls[k] = round(r, 4)
            recall_sums[k] += r

            top_k = ranked_ids[:k]
            same_category = sum(1 for did in top_k if docs_by_id[did]["category"] == expected_category)
            precision = same_category / len(top_k) if top_k else 0.0
            cat_precisions[k] = round(precision, 4)
            category_precision_sums[k] += precision

        per_query.append({
            "query": example["query"],
            "expected_doc_ids": sorted(expected_ids),
            "top_5_retrieved": ranked_ids[:5],
            "reciprocal_rank": round(rr, 4),
            "recall_at_k": recalls,
            "category_precision_at_k": cat_precisions,
        })

    n = len(golden_set)
    return {
        "mode": "mock (TF-IDF)" if mock else "real (OpenAI text-embedding-3-small)",
        "corpus_size": len(docs_by_id),
        "n_queries": n,
        "mrr": round(mrr_sum / n, 4) if n else 0.0,
        "recall_at_k": {str(k): round(recall_sums[k] / n, 4) if n else 0.0 for k in K_VALUES},
        "category_precision_at_k": {
            str(k): round(category_precision_sums[k] / n, 4) if n else 0.0 for k in K_VALUES
        },
        "per_query": per_query,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--real", action="store_true", help="Use real OpenAI embeddings instead of mock TF-IDF (requires OPENAI_API_KEY, costs money)"
    )
    group.add_argument(
        "--both", action="store_true", help="Run mock and real side by side and print a comparison (requires OPENAI_API_KEY)"
    )
    args = parser.parse_args()

    OUTPUTS_DIR.mkdir(exist_ok=True)

    if args.both:
        mock_report = evaluate(mock=True)
        real_report = evaluate(mock=False)
        (OUTPUTS_DIR / "eval_report_retrieval.json").write_text(
            json.dumps({"mock": mock_report, "real": real_report}, indent=2)
        )
        for report in (mock_report, real_report):
            _print_report(report)
        print("\nSaved to outputs/eval_report_retrieval.json")
        return

    report = evaluate(mock=not args.real)
    (OUTPUTS_DIR / "eval_report_retrieval.json").write_text(json.dumps(report, indent=2))
    _print_report(report)
    print("\nSaved to outputs/eval_report_retrieval.json")


def _print_report(report: dict[str, Any]) -> None:
    print(f"=== RETRIEVAL EVAL — {report['mode']} ({report['n_queries']} queries, {report['corpus_size']} docs) ===")
    print(f"MRR:                  {report['mrr']:.4f}")
    for k, v in report["recall_at_k"].items():
        print(f"Recall@{k}:             {v:.0%}")
    for k, v in report["category_precision_at_k"].items():
        print(f"Category precision@{k}: {v:.0%}")


if __name__ == "__main__":
    main()

"""
Golden-set evaluation for the Merchant Intelligence Copilot (src/copilot/).

Runs data/golden_set_copilot.json through the orchestrator graph and reports:
  - route accuracy (exact-set match rate + mean recall of expected tools)
  - citation hallucination rate — every cited policy_doc id must exist in
    the actually-loaded corpus (data/policy_docs.json); should be exactly
    0%, the cheapest high-value regression check there is for a RAG system
  - citation recall — fraction of examples whose expected_citation_ids are
    all present in the answer's citations
  - risk-caveat mention rate — of examples expecting the model's ROC-AUC
    caveat, how many actually got it (DECISIONS.md D24's honesty concern,
    made mechanically checkable)
  - complaint classification accuracy, for the one complaint-routed example
  - facts_ok_rate — fraction of examples where every expected_facts check passed

Always forces the small committed fixture (data/copilot_fixture_transactions.csv),
never the real transactions_sample.csv — the golden set's expected merchant
ids (90001-90004) and citation ids are fixture-specific, so evaluating
against the real ~10k-merchant dataset wouldn't make sense here (unlike
scripts/evaluate_classifier.py, whose golden set doesn't depend on which
merchant dataset is loaded). This also makes it runnable in CI, which never
has the real (gitignored) CSV.

Runs with MOCK_LLM by default — free, deterministic, no API key needed.
`--real` switches to the real router/synthesizer (requires OPENAI_API_KEY,
costs money, not deterministic run-to-run).

This is a small, actually-runnable slice of what a production evaluation
strategy would look like (see DECISIONS.md D10 for the same caveat about
scripts/evaluate_classifier.py's golden set) — 11 examples validate that the
harness mechanism works, not statistical significance.

Usage:
    MOCK_LLM=1 uv run python -m scripts.evaluate_copilot
    OPENAI_API_KEY=sk-... uv run python -m scripts.evaluate_copilot --real
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_SET_PATH = REPO_ROOT / "data" / "golden_set_copilot.json"
OUTPUTS_DIR = REPO_ROOT / "outputs"


def _load_golden_set() -> list[dict[str, Any]]:
    return json.loads(GOLDEN_SET_PATH.read_text())


def evaluate(mock: bool = True) -> dict[str, Any]:
    """Runs the golden set through the copilot graph and computes metrics.

    Sets MOCK_LLM before importing graph-adjacent modules, since
    build_agent() (used by the complaint_classifier tool) branches on that
    env var at call time — mirrors scripts/evaluate_classifier.py's
    evaluate() for the same reason.
    """
    os.environ["MOCK_LLM"] = "1" if mock else "0"

    import src.copilot.tools.data_analyst as data_analyst

    # Always the fixture — see module docstring for why.
    data_analyst.REAL_CSV_PATH = Path("/nonexistent-forced-fixture-only.csv")

    from src.copilot.graph import build_graph
    from src.copilot.state import initial_state
    from src.copilot.tools.grounding import known_policy_ids

    graph = build_graph()
    golden_set = _load_golden_set()
    known_ids = known_policy_ids()

    results: list[dict[str, Any]] = []
    citation_total_cited = 0
    citation_hallucinations = 0
    citation_recall_hits = 0
    citation_recall_total = 0
    caveat_expected_total = 0
    caveat_expected_met = 0
    classification_total = 0
    classification_correct = 0

    for example in golden_set:
        state = initial_state(
            example["question"],
            merchant_id=example["merchant_id"],
            locale=example["locale"],
            mock=mock,
        )
        result = graph.invoke(state)
        answer = result["answer"] or ""

        predicted_route = sorted({tc["tool"] for tc in result["tool_calls"]})
        expected_route = sorted(example["expected_route"])
        route_exact = predicted_route == expected_route
        expected_set, predicted_set = set(expected_route), set(predicted_route)
        route_recall = len(expected_set & predicted_set) / len(expected_set) if expected_set else 1.0

        cited_policy_ids = {c["id"] for c in result["citations"] if c["source_type"] == "policy_doc"}
        for cid in cited_policy_ids:
            citation_total_cited += 1
            if cid not in known_ids:
                citation_hallucinations += 1

        expected_citation_ids = set(example.get("expected_citation_ids", []))
        if expected_citation_ids:
            citation_recall_total += 1
            if expected_citation_ids.issubset(cited_policy_ids):
                citation_recall_hits += 1

        facts = example.get("expected_facts", {})
        facts_ok = True
        if "mentions_auc_caveat" in facts:
            caveat_expected_total += 1
            met = ("0.58" in answer) == facts["mentions_auc_caveat"]
            facts_ok = facts_ok and met
            if met and facts["mentions_auc_caveat"]:
                caveat_expected_met += 1
        if "mentions_merchant_id" in facts:
            mid = example["merchant_id"]
            facts_ok = facts_ok and (mid is not None and str(mid) in answer) == facts["mentions_merchant_id"]
        if "mentions_not_found" in facts:
            facts_ok = facts_ok and ("No transaction history found" in answer) == facts["mentions_not_found"]

        expected_category = example.get("expected_classification_category")
        classification_ok = None
        if expected_category is not None:
            classification_total += 1
            actual_category = result["tool_results"].get("complaint_classifier", {}).get("category")
            classification_ok = actual_category == expected_category
            if classification_ok:
                classification_correct += 1

        results.append({
            "id": example["id"],
            "question": example["question"],
            "expected_route": expected_route,
            "predicted_route": predicted_route,
            "route_exact_match": route_exact,
            "route_recall": round(route_recall, 4),
            "facts_ok": facts_ok,
            "classification_ok": classification_ok,
        })

    n = len(golden_set)
    return {
        "mode": "mock" if mock else "real",
        "n_examples": n,
        "route_exact_match_rate": round(sum(r["route_exact_match"] for r in results) / n, 4) if n else 0.0,
        "route_recall_mean": round(sum(r["route_recall"] for r in results) / n, 4) if n else 0.0,
        "citation_hallucination_rate": (
            round(citation_hallucinations / citation_total_cited, 4) if citation_total_cited else 0.0
        ),
        "citation_recall_rate": (
            round(citation_recall_hits / citation_recall_total, 4) if citation_recall_total else None
        ),
        "risk_caveat_mention_rate": (
            round(caveat_expected_met / caveat_expected_total, 4) if caveat_expected_total else None
        ),
        "classification_accuracy": (
            round(classification_correct / classification_total, 4) if classification_total else None
        ),
        "facts_ok_rate": round(sum(1 for r in results if r["facts_ok"]) / n, 4) if n else 0.0,
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--real", action="store_true", help="Use the real router/synthesizer (requires OPENAI_API_KEY, costs money)"
    )
    args = parser.parse_args()

    report = evaluate(mock=not args.real)

    OUTPUTS_DIR.mkdir(exist_ok=True)
    (OUTPUTS_DIR / "eval_report_copilot.json").write_text(json.dumps(report, indent=2))

    print(f"=== COPILOT EVAL REPORT ({report['mode']} mode, {report['n_examples']} examples) ===")
    print(f"Route exact-match rate:      {report['route_exact_match_rate']:.0%}")
    print(f"Route recall (mean):         {report['route_recall_mean']:.0%}")
    print(f"Citation hallucination rate: {report['citation_hallucination_rate']:.0%}")
    print(f"Citation recall rate:        {report['citation_recall_rate']}")
    print(f"Risk-caveat mention rate:    {report['risk_caveat_mention_rate']}")
    print(f"Classification accuracy:     {report['classification_accuracy']}")
    print(f"Facts-ok rate:               {report['facts_ok_rate']:.0%}")
    print("\nSaved to outputs/eval_report_copilot.json")


if __name__ == "__main__":
    main()

"""
Golden-set evaluation for the complaint classifier (src/parte4_api/).

Runs `data/golden_set.json` through the classifier agent and reports:
  - category accuracy, overall and per-category
  - churn_threat recall — the critical metric per DECISIONS.md D10 (a false
    negative there is a lost merchant with no intervention)
  - prompt-injection detection rate
  - retrieval category-precision@k (does the top-k retrieved historical
    context share the golden example's expected category?)

Runs against MOCK_LLM by default — free, deterministic, no API key needed.
`--real` switches to a real Agent (requires OPENAI_API_KEY, costs money, not
deterministic run-to-run).

This is a small, actually-runnable slice of the evaluation strategy
DECISIONS.md D10 describes at production scale (300-email golden set,
LLM-as-judge, shadow mode) — not a replacement for it.

Usage:
    MOCK_LLM=1 uv run python -m scripts.evaluate_classifier
    OPENAI_API_KEY=sk-... uv run python -m scripts.evaluate_classifier --real
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_SET_PATH = REPO_ROOT / "data" / "golden_set.json"
OUTPUTS_DIR = REPO_ROOT / "outputs"


def _load_golden_set() -> list[dict[str, Any]]:
    return json.loads(GOLDEN_SET_PATH.read_text())


def evaluate(mock: bool = True) -> dict[str, Any]:
    """Runs the golden set through the agent and computes metrics.

    Sets MOCK_LLM before importing agent.py, since build_agent() branches on
    that env var at call time — this makes `evaluate()` usable as a plain
    function (from tests, from other scripts) without the caller having to
    manage the env var themselves.
    """
    os.environ["MOCK_LLM"] = "1" if mock else "0"
    from src.parte4_api.agent import build_agent
    from src.parte4_api.retrieval import retrieve_similar_cases

    agent = build_agent()
    golden_set = _load_golden_set()

    per_category_correct: dict[str, int] = defaultdict(int)
    per_category_total: dict[str, int] = defaultdict(int)
    churn_correct = 0
    churn_total = 0
    injection_detected = 0
    injection_total = 0
    retrieval_hits = 0
    retrieval_total = 0
    urgency_met = 0
    escalation_correct = 0
    results: list[dict[str, Any]] = []

    for example in golden_set:
        result = agent.classify(
            merchant_id=0,
            email_text=example["email_text"],
            locale=example["locale"],
        )
        predicted_category = result["category"]
        correct = predicted_category == example["expected_category"]

        per_category_total[example["expected_category"]] += 1
        if correct:
            per_category_correct[example["expected_category"]] += 1

        if example["expected_category"] == "churn_threat":
            churn_total += 1
            if correct:
                churn_correct += 1

        if example["is_prompt_injection"]:
            injection_total += 1
            if result["reasoning"] == "prompt_injection_detected":
                injection_detected += 1

        # expected_min_urgency is a floor, not an exact target: the model
        # underestimating severity (missing an urgent case) is the failure
        # mode that matters, not overestimating it.
        urgency_ok = result["urgency"] >= example["expected_min_urgency"]
        if urgency_ok:
            urgency_met += 1

        escalation_ok = result["requires_human_escalation"] == example["expected_requires_escalation"]
        if escalation_ok:
            escalation_correct += 1

        similar = retrieve_similar_cases(example["email_text"], k=3, mock=mock)
        if similar:
            retrieval_total += 1
            if any(c["category"] == example["expected_category"] for c in similar):
                retrieval_hits += 1

        results.append(
            {
                "id": example["id"],
                "expected_category": example["expected_category"],
                "predicted_category": predicted_category,
                "correct": correct,
                "urgency_meets_minimum": urgency_ok,
                "escalation_correct": escalation_ok,
            }
        )

    overall_correct = sum(per_category_correct.values())
    overall_total = sum(per_category_total.values())

    return {
        "mode": "mock" if mock else "real",
        "n_examples": overall_total,
        "overall_accuracy": round(overall_correct / overall_total, 4) if overall_total else 0.0,
        "per_category_accuracy": {
            cat: round(per_category_correct[cat] / per_category_total[cat], 4)
            for cat in per_category_total
        },
        "churn_threat_recall": round(churn_correct / churn_total, 4) if churn_total else None,
        "prompt_injection_detection_rate": (
            round(injection_detected / injection_total, 4) if injection_total else None
        ),
        "retrieval_category_precision_at_k": (
            round(retrieval_hits / retrieval_total, 4) if retrieval_total else None
        ),
        "urgency_meets_minimum_rate": round(urgency_met / overall_total, 4) if overall_total else None,
        "escalation_accuracy": round(escalation_correct / overall_total, 4) if overall_total else None,
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--real", action="store_true", help="Use the real Agent (requires OPENAI_API_KEY, costs money)"
    )
    args = parser.parse_args()

    report = evaluate(mock=not args.real)

    OUTPUTS_DIR.mkdir(exist_ok=True)
    (OUTPUTS_DIR / "eval_report.json").write_text(json.dumps(report, indent=2))

    print(f"=== EVAL REPORT ({report['mode']} mode, {report['n_examples']} examples) ===")
    print(f"Overall accuracy: {report['overall_accuracy']:.0%}")
    print("Per-category accuracy:")
    for cat, acc in report["per_category_accuracy"].items():
        print(f"  {cat:<16}: {acc:.0%}")
    print(f"churn_threat recall:            {report['churn_threat_recall']}")
    print(f"Prompt injection detection rate:{report['prompt_injection_detection_rate']}")
    print(f"Retrieval category precision@k: {report['retrieval_category_precision_at_k']}")
    print(f"Urgency >= expected minimum:     {report['urgency_meets_minimum_rate']}")
    print(f"Escalation flag accuracy:        {report['escalation_accuracy']}")
    print("\nSaved to outputs/eval_report.json")


if __name__ == "__main__":
    main()

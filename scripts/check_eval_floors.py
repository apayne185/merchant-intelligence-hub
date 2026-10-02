"""
CI gate: fails the build if the eval report regresses below hard floors.

Reads outputs/eval_report.json, written earlier in the same CI run by
scripts/evaluate_copilot.py. Under MOCK_LLM=1 the whole pipeline is
deterministic, so any drop below a floor is a code change breaking
something, not noise. Floors sit at the committed baseline values.

    uv run python -m scripts.check_eval_floors
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUTS_DIR = REPO_ROOT / "outputs"
REPORT = "eval_report.json"

FLOORS: dict[str, float] = {
    "route_accuracy": 1.0,
    "figure_accuracy": 1.0,
    "verification_pass_rate": 1.0,  # nosec B105 - metric name, not a credential
    "retrieval_hit_rate": 0.875,
    "decision_accuracy": 1.0,
    "gap_honesty_rate": 1.0,
    "verifier_recall": 1.0,
}
CEILINGS: dict[str, float] = {
    "numeric_hallucination_rate": 0.0,
    "verifier_false_positive_rate": 0.0,
    "http_errors": 0,
}


def check(summary: dict[str, Any]) -> list[str]:
    failures = []
    for metric, floor in FLOORS.items():
        if metric not in summary:
            failures.append(f"{metric} missing from report")
        elif summary[metric] is not None and summary[metric] < floor:
            failures.append(f"{metric} = {summary[metric]} < floor {floor}")
    for metric, ceiling in CEILINGS.items():
        if metric not in summary:
            failures.append(f"{metric} missing from report")
        elif summary[metric] is not None and summary[metric] > ceiling:
            failures.append(f"{metric} = {summary[metric]} > ceiling {ceiling}")
    return failures


def main() -> None:
    path = OUTPUTS_DIR / REPORT
    if not path.exists():
        print(f"EVAL FLOOR CHECK FAILED: {REPORT} not found; run scripts.evaluate_copilot first")
        sys.exit(1)
    failures = check(json.loads(path.read_text())["summary"])
    if failures:
        print("EVAL FLOOR CHECK FAILED:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print(f"eval floors OK ({len(FLOORS)} floors, {len(CEILINGS)} ceilings)")


if __name__ == "__main__":
    main()

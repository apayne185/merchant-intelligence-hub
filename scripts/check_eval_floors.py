"""
CI gate: asserts both eval reports meet hard reliability floors.

Reads outputs/eval_report.json (complaint classifier, D21) and
outputs/eval_report_copilot.json (copilot, D28) — both regenerated earlier
in the same CI run — and fails (exit 1) if any floor isn't met. Floors are
anchored to the values committed alongside each harness, not arbitrary
numbers: both harnesses are fully deterministic under MOCK_LLM=1, so a
regression below a rule-based mock's own current value means an actual code
change broke something, not run-to-run noise.

This is what turns "the eval harness runs" (informational) into "the eval
harness gates merges" — the CI-gate-would-have-caught-it story from the
original brief, made real. See DECISIONS.md D29.

Usage:
    uv run python -m scripts.check_eval_floors
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUTS_DIR = REPO_ROOT / "outputs"

# (report file, metric key, required minimum) — anchored to the value
# committed alongside each harness (see D21, D28).
FLOORS = [
    ("eval_report.json", "prompt_injection_detection_rate", 1.0),
    ("eval_report.json", "churn_threat_recall", 0.5),
    ("eval_report.json", "retrieval_category_precision_at_k", 0.85),
    ("eval_report_copilot.json", "route_exact_match_rate", 1.0),
    ("eval_report_copilot.json", "risk_caveat_mention_rate", 1.0),
    ("eval_report_copilot.json", "classification_accuracy", 1.0),
]

# (report file, metric key, required maximum) — a ceiling, not a floor.
CEILINGS = [
    ("eval_report_copilot.json", "citation_hallucination_rate", 0.0),
]


def _load(filename: str) -> dict | None:
    path = OUTPUTS_DIR / filename
    if not path.exists():
        return None
    return json.loads(path.read_text())


def main() -> None:
    failures: list[str] = []
    reports: dict[str, dict] = {}

    for filename, metric, minimum in FLOORS:
        report = reports.setdefault(filename, _load(filename))
        if report is None:
            failures.append(f"{filename} not found — run the eval script first")
            continue
        value = report.get(metric)
        if value is None:
            failures.append(f"{filename}: metric '{metric}' missing from report")
        elif value < minimum:
            failures.append(f"{filename}: {metric} = {value} < required floor {minimum}")

    for filename, metric, maximum in CEILINGS:
        report = reports.setdefault(filename, _load(filename))
        if report is None:
            continue  # already reported above
        value = report.get(metric)
        if value is None:
            failures.append(f"{filename}: metric '{metric}' missing from report")
        elif value > maximum:
            failures.append(f"{filename}: {metric} = {value} > allowed ceiling {maximum}")

    if failures:
        print("EVAL FLOOR CHECK FAILED:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)

    print("All eval floors met:")
    for filename, metric, minimum in FLOORS:
        print(f"  {filename}: {metric} = {reports[filename][metric]} (>= {minimum})")
    for filename, metric, maximum in CEILINGS:
        print(f"  {filename}: {metric} = {reports[filename][metric]} (<= {maximum})")


if __name__ == "__main__":
    main()

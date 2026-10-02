"""
Golden-set evaluation of the copilot, plus an adversarial test of the answer verifier.

    MOCK_LLM=1 uv run python -m scripts.evaluate_copilot          # deterministic, CI gate
    OPENAI_API_KEY=... uv run python -m scripts.evaluate_copilot --real

Requests go through the real FastAPI app (guardrails, graph, verification),
not the graph alone. Metrics, written to outputs/eval_report.json:

  route_accuracy            exact match of the tools that ran
  figure_accuracy           expected SEC figures present in the evidence AND stated
                            in the answer at a precision consistent with them
  verification_pass_rate    answers whose every number verified (or had none)
  numeric_hallucination_rate  unverified numbers / numbers checked, over all answers
  retrieval_hit_rate        a passage from the right company containing an expected keyword
  decision_accuracy         pre-trade APPROVE/REJECT matches
  gap_honesty_rate          unanswerable questions say so instead of inventing a figure
  verifier_recall           corrupted answers (each number perturbed) the verifier rejects
  verifier_false_positive_rate  correct answers the verifier rejects
  latency_ms_p50 / p95      end-to-end /ask latency

Expected figures come straight from SEC companyfacts by concept and period
end date, not from the normalizer under test (see data/golden_set.json).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_PATH = REPO_ROOT / "data" / "golden_set.json"
REPORT_PATH = REPO_ROOT / "outputs" / "eval_report.json"
PERTURBATIONS = (0.005, -0.01, 0.05, -0.10, 0.5)


def _figure_ok(expected: float, evidence: list[dict[str, Any]], answer: str) -> bool:
    from src.copilot.verification import extract_claims

    in_evidence = any(
        abs(v - expected) <= 1e-9 * max(1.0, abs(expected)) for ev in evidence for v in ev["values"].values()
    )
    stated = any(abs(abs(c.value) - abs(expected)) <= c.tolerance + 1e-9 * abs(expected)
                 for c in extract_claims(answer))
    return in_evidence and stated


def _perturb(answer: str, evidence: list[dict[str, Any]]) -> list[str]:
    """Corrupted copies of `answer`, one number changed in each. Perturbations
    that still match some evidence value at the stated precision are skipped:
    they are not wrong, so a verifier accepting them is correct."""
    from src.copilot.schemas import Evidence
    from src.copilot.verification import _matches, evidence_values, extract_claims

    values = evidence_values([Evidence(**e) for e in evidence])
    out = []
    for claim in extract_claims(answer):
        idx = claim.start
        m = re.search(r"\d[\d,]*(?:\.\d+)?", claim.text)
        if m is None:
            continue
        digits = m.group(0)
        decimals = len(digits.split(".")[1]) if "." in digits else 0
        base = float(digits.replace(",", ""))
        variants = [f"{base * (1 + d):,.{decimals}f}" for d in PERTURBATIONS]
        plain = digits.replace(",", "").replace(".", "")
        if len(plain) >= 2 and plain[0] != plain[1]:  # transpose the first two digits
            swapped = plain[1] + plain[0] + plain[2:]
            variants.append(f"{float(swapped) / 10 ** decimals:,.{decimals}f}")
        for v in variants:
            new_text = claim.text.replace(digits, v, 1)
            new_claims = extract_claims(new_text)
            if new_text == claim.text or not new_claims or _matches(new_claims[0], values):
                continue
            out.append(answer[:idx] + new_text + answer[claim.end:])
    return out


def run(real: bool) -> dict[str, Any]:
    if not real:
        os.environ["MOCK_LLM"] = "1"
    from fastapi.testclient import TestClient
    from src.copilot.api import app
    from src.copilot.schemas import Evidence
    from src.copilot.verification import verify_answer

    cases = json.loads(GOLDEN_PATH.read_text())["cases"]
    rows: list[dict[str, Any]] = []
    with TestClient(app) as client:
        for case in cases:
            t0 = time.perf_counter()
            resp = client.post("/ask", json=case["request"])
            latency = (time.perf_counter() - t0) * 1000
            body = resp.json()
            row: dict[str, Any] = {"id": case["id"], "category": case["category"], "status_code": resp.status_code,
                                   "latency_ms": round(latency, 1)}
            if resp.status_code != 200:
                rows.append(row)
                continue
            answer, evidence, ver = body["answer"], body["evidence"], body["verification"]
            row.update({
                "route": body["route"], "route_ok": sorted(body["route"]) == sorted(case["expected_route"]),
                "verification": ver["status"], "numbers_checked": ver["numbers_checked"],
                "numbers_unverified": len(ver["unverified"]), "fallback_used": ver["fallback_used"],
                "answer": answer,
            })
            if "expected_figures" in case:
                row["figures_ok"] = [_figure_ok(f["value"], evidence, answer) for f in case["expected_figures"]]
            if "retrieval" in case:
                spec = case["retrieval"]
                row["retrieval_hit"] = any(
                    ev["kind"] == "filing_passage" and ev["ticker"] == spec["ticker"]
                    and any(k.lower() in (ev.get("excerpt") or "").lower() for k in spec["keywords"])
                    for ev in evidence
                )
            if "expected_decision" in case:
                row["decision_ok"] = f"Pre-trade decision: {case['expected_decision']}" in answer
            if "expected_gaps" in case:
                row["gap_ok"] = all(g.lower() in answer.lower() for g in case["expected_gaps"])

            # Adversarial: every corrupted variant of this answer must fail verification.
            evs = [Evidence(**e) for e in evidence]
            corrupted = _perturb(answer, evidence)
            row["verifier_adversarial"] = len(corrupted)
            row["verifier_caught"] = sum(verify_answer(a, evs).status == "failed" for a in corrupted)
            row["verifier_false_positive"] = verify_answer(answer, evs).status == "failed"
            rows.append(row)

    ok = [r for r in rows if r["status_code"] == 200]

    def rate(key: str) -> float | None:
        vals = [v for r in ok if key in r for v in (r[key] if isinstance(r[key], list) else [r[key]])]
        return round(sum(vals) / len(vals), 4) if vals else None

    checked = sum(r["numbers_checked"] for r in ok)
    adversarial = sum(r["verifier_adversarial"] for r in ok)
    lat = sorted(r["latency_ms"] for r in rows)
    summary = {
        "mode": "real" if real else "mock",
        "cases": len(cases),
        "http_errors": len(rows) - len(ok),
        "route_accuracy": rate("route_ok"),
        "figure_accuracy": rate("figures_ok"),
        "verification_pass_rate": round(sum(r["verification"] != "failed" for r in ok) / len(ok), 4),
        "numeric_hallucination_rate": round(sum(r["numbers_unverified"] for r in ok) / checked, 4) if checked else 0.0,
        "fallback_rate": round(sum(r["fallback_used"] for r in ok) / len(ok), 4),
        "retrieval_hit_rate": rate("retrieval_hit"),
        "decision_accuracy": rate("decision_ok"),
        "gap_honesty_rate": rate("gap_ok"),
        "numbers_checked": checked,
        "verifier_adversarial_cases": adversarial,
        "verifier_recall": round(sum(r["verifier_caught"] for r in ok) / adversarial, 4) if adversarial else None,
        "verifier_false_positive_rate": rate("verifier_false_positive"),
        "latency_ms_p50": round(statistics.median(lat), 1),
        "latency_ms_p95": round(lat[min(len(lat) - 1, int(0.95 * len(lat)))], 1),
    }
    return {"summary": summary, "cases": rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true", help="call the real LLM (needs OPENAI_API_KEY)")
    args = ap.parse_args()
    report = run(args.real)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=1) + "\n")
    for k, v in report["summary"].items():
        print(f"{k:32s} {v}")
    failing = [r["id"] for r in report["cases"] if r.get("route_ok") is False or False in r.get("figures_ok", [])
               or r.get("retrieval_hit") is False or r.get("decision_ok") is False or r.get("gap_ok") is False]
    if failing:
        print("cases with misses:", ", ".join(failing))


if __name__ == "__main__":
    main()

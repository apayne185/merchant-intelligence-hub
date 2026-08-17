"""
Tests for scripts/check_eval_floors.py.

Found via code review that this script had zero test coverage, and a real
bug: `report.get(metric) is None` treated a genuinely-missing key (a script
bug) identically to a key legitimately present with value None (some
copilot metrics are None when zero golden-set examples exercise them —
see scripts/evaluate_copilot.py). These tests pin both behaviors so they
can't quietly re-merge.

main() only calls sys.exit(1) on failure — on success it just returns
normally (no explicit sys.exit(0)), so only the failure-path tests wrap
the call in pytest.raises(SystemExit).
"""
from __future__ import annotations

import json

import pytest
import scripts.check_eval_floors as check_eval_floors


@pytest.fixture
def outputs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(check_eval_floors, "OUTPUTS_DIR", tmp_path)
    return tmp_path


def _write(outputs_dir, filename: str, data: dict) -> None:
    (outputs_dir / filename).write_text(json.dumps(data))


def _passing_reports() -> tuple[dict, dict]:
    classifier = {
        "prompt_injection_detection_rate": 1.0,
        "churn_threat_recall": 0.5,
        "retrieval_category_precision_at_k": 0.8929,
    }
    copilot = {
        "route_exact_match_rate": 1.0,
        "risk_caveat_mention_rate": 1.0,
        "classification_accuracy": 1.0,
        "citation_hallucination_rate": 0.0,
    }
    return classifier, copilot


def test_main_succeeds_when_all_floors_met(outputs_dir, capsys) -> None:
    classifier, copilot = _passing_reports()
    _write(outputs_dir, "eval_report.json", classifier)
    _write(outputs_dir, "eval_report_copilot.json", copilot)

    check_eval_floors.main()  # must not raise

    assert "All eval floors met" in capsys.readouterr().out


def test_main_exits_nonzero_when_a_floor_is_missed(outputs_dir) -> None:
    classifier, copilot = _passing_reports()
    classifier["churn_threat_recall"] = 0.1  # below the 0.5 floor
    _write(outputs_dir, "eval_report.json", classifier)
    _write(outputs_dir, "eval_report_copilot.json", copilot)

    with pytest.raises(SystemExit) as exc:
        check_eval_floors.main()
    assert exc.value.code == 1


def test_main_exits_nonzero_when_a_report_file_is_missing(outputs_dir) -> None:
    classifier, _ = _passing_reports()
    _write(outputs_dir, "eval_report.json", classifier)
    # eval_report_copilot.json deliberately not written.

    with pytest.raises(SystemExit) as exc:
        check_eval_floors.main()
    assert exc.value.code == 1


def test_metric_legitimately_none_is_skipped_not_a_failure(outputs_dir, capsys) -> None:
    # evaluate_copilot.py writes None for a metric when zero golden-set
    # examples exercise it — this must not be treated as a failure.
    classifier, copilot = _passing_reports()
    copilot["risk_caveat_mention_rate"] = None
    _write(outputs_dir, "eval_report.json", classifier)
    _write(outputs_dir, "eval_report_copilot.json", copilot)

    check_eval_floors.main()  # must not raise

    assert "skipped, not applicable this run" in capsys.readouterr().out


def test_metric_genuinely_missing_key_is_a_failure_not_skipped(outputs_dir) -> None:
    # A metric key that's plain absent (not present-with-value-None) is a
    # real script bug, not "not applicable" — must still fail.
    classifier, copilot = _passing_reports()
    del copilot["risk_caveat_mention_rate"]
    _write(outputs_dir, "eval_report.json", classifier)
    _write(outputs_dir, "eval_report_copilot.json", copilot)

    with pytest.raises(SystemExit) as exc:
        check_eval_floors.main()
    assert exc.value.code == 1


def test_ceiling_metric_legitimately_none_is_skipped(outputs_dir) -> None:
    classifier, copilot = _passing_reports()
    copilot["citation_hallucination_rate"] = None
    _write(outputs_dir, "eval_report.json", classifier)
    _write(outputs_dir, "eval_report_copilot.json", copilot)

    check_eval_floors.main()  # must not raise


def test_against_real_committed_reports() -> None:
    # Sanity check against this repo's own actual committed baseline (no
    # outputs_dir fixture — OUTPUTS_DIR is the real, unpatched path here)
    # — catches the case where check_eval_floors.py and the real
    # outputs/*.json have drifted apart.
    check_eval_floors.main()  # must not raise

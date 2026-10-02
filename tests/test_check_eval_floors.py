"""Tests for the CI eval gate (scripts/check_eval_floors.py)."""
from __future__ import annotations

import json

import pytest
import scripts.check_eval_floors as gate


@pytest.fixture
def outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "OUTPUTS_DIR", tmp_path)
    return tmp_path


def _passing() -> dict:
    return {**{k: v for k, v in gate.FLOORS.items()}, **{k: v for k, v in gate.CEILINGS.items()}}


def _write(outputs, summary: dict) -> None:
    (outputs / gate.REPORT).write_text(json.dumps({"summary": summary, "cases": []}))


def test_passes_at_exact_floors(outputs, capsys) -> None:
    _write(outputs, _passing())
    gate.main()
    assert "eval floors OK" in capsys.readouterr().out


@pytest.mark.parametrize("metric,value", [("figure_accuracy", 0.97), ("verifier_recall", 0.99),
                                          ("numeric_hallucination_rate", 0.01), ("http_errors", 1)])
def test_fails_on_regression(outputs, metric, value) -> None:
    _write(outputs, {**_passing(), metric: value})
    with pytest.raises(SystemExit):
        gate.main()


def test_missing_report_fails(outputs) -> None:
    with pytest.raises(SystemExit):
        gate.main()


def test_missing_key_fails_but_none_is_skipped() -> None:
    summary = _passing()
    summary["retrieval_hit_rate"] = None  # legitimately not applicable
    assert gate.check(summary) == []
    del summary["route_accuracy"]
    assert gate.check(summary) == ["route_accuracy missing from report"]


def test_committed_report_meets_floors() -> None:
    summary = json.loads((gate.REPO_ROOT / "outputs" / gate.REPORT).read_text())["summary"]
    assert gate.check(summary) == []

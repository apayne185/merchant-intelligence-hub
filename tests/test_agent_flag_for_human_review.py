"""
Tests for src/parte4_api/agent.py's flag_for_human_review() — specifically
its size-cap/rotation of outputs/human_review_queue.jsonl, which had no
cap at all before: same class of issue as tracing.py's JsonLinesFileExporter
(an append-only .jsonl sink populated by a real side effect, growing
without bound over a long-running process).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from src.parte4_api import agent as agent_module
from src.parte4_api.agent import flag_for_human_review


@pytest.fixture(autouse=True)
def _isolated_outputs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_module, "OUTPUTS_DIR", tmp_path)


def test_flag_for_human_review_appends_below_cap(tmp_path: Path) -> None:
    flag_for_human_review(90001, "reason one")
    flag_for_human_review(90001, "reason two")

    queue_path = tmp_path / "human_review_queue.jsonl"
    lines = queue_path.read_text().strip().split("\n")
    assert len(lines) == 2
    assert not (tmp_path / "human_review_queue.jsonl.1").exists()


def test_flag_for_human_review_rotates_past_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_module, "_MAX_QUEUE_FILE_BYTES", 10)

    flag_for_human_review(90001, "first reason")
    queue_path = tmp_path / "human_review_queue.jsonl"
    first_content = queue_path.read_text()

    flag_for_human_review(90002, "second reason")  # should rotate before writing

    backup = tmp_path / "human_review_queue.jsonl.1"
    assert backup.exists()
    assert backup.read_text() == first_content
    assert "90002" in queue_path.read_text()
    assert "90001" not in queue_path.read_text()


def test_flag_for_human_review_returns_expected_shape() -> None:
    result = flag_for_human_review(90001, "some reason")
    assert result == {"queued": True, "merchant_id": 90001}

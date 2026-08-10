"""Shared pytest fixtures for the test suite."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def force_fixture_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forces src.copilot.tools.data_analyst to fall back to the small
    committed fixture even if the real (gitignored) transactions_sample.csv
    happens to be present on the machine running the tests — so graph-level
    tests are deterministic regardless of local environment, matching what
    CI actually sees.
    """
    import src.copilot.tools.data_analyst as data_analyst

    monkeypatch.setattr(data_analyst, "REAL_CSV_PATH", Path("/nonexistent-forced-fixture-only.csv"))

"""Shared pytest configuration: every test runs offline in mock LLM mode."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _mock_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOCK_LLM", "1")

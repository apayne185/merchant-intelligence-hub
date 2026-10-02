"""LLM mode switch: MOCK_LLM=1 runs every LLM call path offline and deterministically."""
from __future__ import annotations

import os


def is_mock_mode() -> bool:
    return os.environ.get("MOCK_LLM", "").lower() in {"1", "true", "yes"}

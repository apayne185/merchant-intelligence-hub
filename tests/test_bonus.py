"""
Test para la Parte 5 · Bonus (src/parte5_bonus.py).

detect_collusion_rings es un stub documentado (ver DECISIONS.md D12) — este
test solo verifica que se comporta como tal, no que detecte colusión real.
"""
from __future__ import annotations

from src.parte5_bonus import detect_collusion_rings


def test_detect_collusion_rings_returns_empty_list() -> None:
    result = detect_collusion_rings()
    assert result == []

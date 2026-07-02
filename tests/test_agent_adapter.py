"""
Test del adaptador para el Agent real de Agno (src/parte4_api/agent.py).

No requiere OPENAI_API_KEY: mockea `Agent.run` para verificar que
`_RealAgentAdapter.classify(...)` expone la misma interfaz que `_MockAgent`
sin hacer una llamada real a OpenAI.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.parte4_api.agent import _LLMClassification, _RealAgentAdapter, build_agent
from src.parte4_api.schemas import Category


class _FakeRunOutput:
    def __init__(self, content: _LLMClassification) -> None:
        self.content = content


@pytest.fixture
def real_agent_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forces the non-mock path for this test only; reverted automatically after."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setenv("MOCK_LLM", "0")


def test_build_agent_returns_real_adapter_without_mock(real_agent_env: None) -> None:
    agent = build_agent()
    assert isinstance(agent, _RealAgentAdapter)


def test_real_adapter_classify_maps_run_output_to_dict(real_agent_env: None) -> None:
    agent = build_agent()
    fake_content = _LLMClassification(
        category=Category.churn_threat,
        urgency=4,
        requires_human_escalation=True,
        reasoning="menciona intención de cancelar",
        merchant_context_used=True,
    )
    with patch.object(agent._agent, "run", return_value=_FakeRunOutput(fake_content)) as mock_run:
        result = agent.classify(merchant_id=10063716, email_text="voy a cancelar", locale="es")

    mock_run.assert_called_once()
    assert result["merchant_id"] == 10063716
    assert result["category"] == Category.churn_threat
    assert result["urgency"] == 4
    assert result["requires_human_escalation"] is True
    assert result["merchant_context_used"] is True

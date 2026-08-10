"""
Complaint classifier tool — thin wrapper around the existing Agno agent.

Reuses src.parte4_api.agent.build_agent() as-is rather than re-implementing
classification: build_agent() already contains the full mock/real split
(_MockAgent / _RealAgentAdapter, both keyed off is_mock_mode()), so this
wrapper inherits that behavior for free instead of needing its own MOCK_LLM
branch. See DECISIONS.md D25.

Router callers: only route a question here when it's an actual pasted
complaint (a merchant's own words), not an analytical question like "which
merchants are at risk" — this agent's job is to classify *one complaint*,
not answer questions about merchants in general. Routing an analytical
question here would misclassify it as `other`/low-urgency instead of
answering it.
"""
from __future__ import annotations

from typing import Any

from src.parte4_api.agent import build_agent


def classify_complaint(merchant_id: int, text: str, locale: str = "en") -> dict[str, Any]:
    """Classifies `text` as a merchant complaint via the existing Agno
    agent — same interface/return shape as agent.py's `_MockAgent`/
    `_RealAgentAdapter.classify()`, since this just delegates to it.
    """
    agent = build_agent()
    return agent.classify(merchant_id=merchant_id, email_text=text, locale=locale)

"""Filings & Risk Copilot: multi-agent orchestration over SEC filings and market risk.

Routes questions to specialist tools (point-in-time XBRL fundamentals, 10-K
risk-factor retrieval, C++ portfolio risk, pre-trade limit checks) through a
LangGraph orchestrator, then verifies every number in the answer against
the evidence those tools produced.
"""

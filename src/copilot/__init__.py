"""Merchant Intelligence Copilot — multi-agent orchestrator.

Routes natural-language merchant questions to specialist tools (KPI/SQL,
churn-risk scoring, policy-doc RAG, complaint classification) via a
LangGraph graph, and returns a structured, cited answer. See
src/copilot/README.md for the architecture.
"""

"""
Synthesis — turns tool_results/citations into the final answer text.

Mock mode: deterministic string templating, zero LLM calls, fully
reproducible and assertable in tests/eval. Real mode: an Agno agent
instructed to state ONLY facts present in tool_results/citations, explicitly
forbidding invented numbers or citation ids — this is the grounding contract
the eval harness's citation-hallucination check (scripts/evaluate_copilot.py)
verifies mechanically. See DECISIONS.md D26.
"""
from __future__ import annotations

from typing import Any

from src.copilot.state import CopilotState


def _fmt_pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def _summarize_data_analyst(results: dict[str, Any], merchant_id: int | None) -> list[str]:
    parts = []
    top = results.get("top_merchants_by_tpv") or []
    if top:
        best = top[0]
        parts.append(
            f"Top merchant by recent TPV is {best['merchant_id']} "
            f"(TPV {best['tpv']:.2f}, approval rate {_fmt_pct(best['approval_rate'])})."
        )
    segments = results.get("churn_rate_by_segment") or []
    if segments:
        worst = max(segments, key=lambda r: r["pct_churn"])
        parts.append(f"Highest churn rate by segment: {worst['segment']} at {_fmt_pct(worst['pct_churn'])}.")
    yoy = results.get("yoy_tpv_by_month") or []
    declines = [r for r in yoy if r["tpv_yoy_pct"] is not None and r["tpv_yoy_pct"] < 0]
    if declines:
        worst = min(declines, key=lambda r: r["tpv_yoy_pct"])
        # yoy_tpv_by_month is only ever computed for a specific merchant_id
        # (see data_analyst_node) — name it explicitly, otherwise "TPV fell
        # X% YoY" reads as if it's about the "top merchant" sentence above,
        # which can be a different merchant entirely.
        who = f"merchant {merchant_id}" if merchant_id is not None else "this merchant"
        parts.append(f"TPV for {who} fell {_fmt_pct(worst['tpv_yoy_pct'])} YoY in {worst['month']}.")
    return parts


def _summarize_risk(results: dict[str, Any]) -> list[str]:
    parts = []
    for s in results.get("scores", []):
        if not s["found"]:
            parts.append(f"No transaction history found for merchant {s['merchant_id']}.")
            continue
        drivers = ", ".join(d["feature"] for d in s["top_drivers"])
        parts.append(
            f"Merchant {s['merchant_id']} churn risk: {s['risk_tier']} "
            f"({_fmt_pct(s['churn_probability'])} probability), top drivers: {drivers}."
        )
    if results.get("scores"):
        parts.append(results["scores"][0]["caveat"])
    return parts


def _summarize_grounding(docs: list[dict[str, Any]]) -> list[str]:
    return [f"Per policy {d['id']} ({d['title']}): {d['text']}" for d in docs]


def _summarize_complaint_classifier(result: dict[str, Any]) -> list[str]:
    return [f"Classified as {result['category']} (urgency {result['urgency']}): {result['reasoning']}"]


def synthesize_mock(state: CopilotState) -> str:
    """Deterministic template over tool_results — no LLM call. Walks
    results in a fixed tool order so output is reproducible run-to-run."""
    results = state["tool_results"]
    parts: list[str] = []

    if "data_analyst" in results:
        parts += _summarize_data_analyst(results["data_analyst"], merchant_id=state["merchant_id"])
    if "risk" in results:
        parts += _summarize_risk(results["risk"])
    if "grounding" in results:
        parts += _summarize_grounding(results["grounding"])
    if "complaint_classifier" in results:
        parts += _summarize_complaint_classifier(results["complaint_classifier"])

    if not parts:
        return "No information was found for this question."
    return " ".join(parts)


def synthesize_real(state: CopilotState) -> str:
    """Real-mode synthesis: an Agno agent grounded strictly in
    tool_results/citations already gathered — no tools attached, it doesn't
    call anything itself, just writes up what the specialist nodes found."""
    from agno.agent import Agent
    from agno.models.openai import OpenAIChat

    instructions = """
You answer merchant-intelligence questions using ONLY the tool results and
citations provided below. Never invent numbers, merchant ids, or citation
ids that aren't present in the provided data. If a churn-risk score is
present, you MUST include its caveat about model reliability. If the data
doesn't answer the question, say so plainly rather than guessing. Keep the
answer under 1200 characters, plain text, no markdown.
"""
    prompt = (
        f"Question: {state['question']}\n\n"
        f"Tool results: {state['tool_results']}\n\n"
        f"Citations: {state['citations']}"
    )
    agent = Agent(model=OpenAIChat(id="gpt-4o-mini"), instructions=instructions)
    run_output = agent.run(prompt)
    return str(run_output.content)


def synthesize_node(state: CopilotState) -> dict:
    answer = synthesize_mock(state) if state["mock"] else synthesize_real(state)
    return {"answer": answer[:1500]}

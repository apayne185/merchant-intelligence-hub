"""
Synthesis: writes the answer, then proves every number in it.

Mock mode joins the tools' pre-formatted findings (deterministic, correct by
construction). Real mode asks the LLM to write from the same findings and
evidence, then runs the numeric verifier:

    draft -> verify -> pass: return
                    -> fail: regenerate once, told exactly which numbers had no source
                             -> pass: return
                             -> fail: return the deterministic answer (fallback_used=True)

So a hallucinated figure can cost a retry or a less fluent answer, but it
cannot reach the caller. Time to first token and token spend are recorded
for every LLM call.
"""
from __future__ import annotations

import time
from typing import Any

from src.copilot.infra.metrics import LLM_TTFT, VERIFICATION_OUTCOMES, record_llm_usage
from src.copilot.router import TOOL_ORDER
from src.copilot.schemas import Evidence, VerificationReport
from src.copilot.state import CopilotState
from src.copilot.verification import verify_answer

SYNTHESIS_MODEL = "gpt-4o-mini"
MAX_ANSWER_CHARS = 2400

_INSTRUCTIONS = """
You are a financial research assistant. Answer ONLY from the findings and
evidence provided. Rules:
- Every number you write must appear in the evidence (same value, rounded no
  more coarsely than shown in the findings). Never compute new figures.
- Money as "$416.16 billion", ratios as "26.9%", multiples as "1.23x".
- Cite the evidence id in square brackets after each fact, e.g. [AAPL:revenue:FY2025:0000320193-25-000079].
- If something asked is missing from the evidence, say it is not available.
- Plain text, no markdown, under 1500 characters.
"""


def template_answer(state: CopilotState) -> str:
    parts: list[str] = []
    for tool in TOOL_ORDER:
        parts.extend(state["findings"].get(tool, []))
    parts.extend(state["gaps"])
    return " ".join(parts) if parts else "No information was found for this question."


def _evidence_digest(evidence: list[dict[str, Any]]) -> str:
    lines = []
    for ev in evidence:
        vals = ", ".join(f"{k}={v:.6g}" for k, v in ev.get("values", {}).items())
        excerpt = f' excerpt="{ev["excerpt"][:300]}"' if ev.get("excerpt") else ""
        lines.append(f"- [{ev['id']}] {ev['label']} ({ev.get('unit') or ''}) {vals}{excerpt}")
    return "\n".join(lines)


def _llm(prompt: str, locale: str, call: str) -> str:  # pragma: no cover - network
    from agno.agent import Agent
    from agno.models.openai import OpenAIChat
    from agno.run.agent import RunCompletedEvent, RunContentEvent

    language = "Spanish" if locale == "es" else "English"
    agent = Agent(model=OpenAIChat(id=SYNTHESIS_MODEL), instructions=_INSTRUCTIONS + f"\nWrite in {language}.")
    t0 = time.perf_counter()
    first = None
    chunks: list[str] = []
    for event in agent.run(prompt, stream=True, stream_events=True):
        if isinstance(event, RunContentEvent) and event.content:
            if first is None:
                first = time.perf_counter() - t0
                LLM_TTFT.labels(model=SYNTHESIS_MODEL, call=call).observe(first)
            chunks.append(str(event.content))
        elif isinstance(event, RunCompletedEvent):
            record_llm_usage(call, SYNTHESIS_MODEL, event)
    return "".join(chunks)


def synthesize_real(state: CopilotState, evidence: list[Evidence]) -> tuple[str, VerificationReport]:
    findings = "\n".join(f"- {f}" for tool in TOOL_ORDER for f in state["findings"].get(tool, []))
    prompt = (
        f"Question: {state['question']}\n\nFindings:\n{findings}\n\nGaps: {state['gaps']}\n\n"
        f"Evidence:\n{_evidence_digest(state['evidence'])}"
    )
    draft = _llm(prompt, state["locale"], "synthesis")
    report = verify_answer(draft, evidence)
    if report.status != "failed":
        return draft, report

    bad = ", ".join(u.text for u in report.unverified)
    retry_prompt = (
        f"{prompt}\n\nYour previous answer contained numbers that are not in the evidence: {bad}. "
        "Rewrite it using only numbers that appear in the evidence."
    )
    draft = _llm(retry_prompt, state["locale"], "synthesis_retry")
    report = verify_answer(draft, evidence)
    report.attempts = 2
    if report.status != "failed":
        return draft, report

    answer = template_answer(state)
    fallback = verify_answer(answer, evidence)
    fallback.fallback_used = True
    fallback.attempts = 2
    return answer, fallback


def synthesize_node(state: CopilotState) -> dict[str, Any]:
    evidence = [Evidence(**e) for e in state["evidence"]]
    if state["mock"]:
        answer = template_answer(state)
        report = verify_answer(answer, evidence)
    else:
        answer, report = synthesize_real(state, evidence)
    outcome = "fallback" if report.fallback_used else report.status
    VERIFICATION_OUTCOMES.labels(outcome=outcome).inc()
    return {"answer": answer[:MAX_ANSWER_CHARS], "verification": report.model_dump()}

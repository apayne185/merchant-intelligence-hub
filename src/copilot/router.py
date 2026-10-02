"""
Router: picks the specialist tools and resolves entities for a question.

Entity extraction (tickers, fiscal year, metrics, weights) is deterministic
in both modes. Tool selection is keyword rules in mock mode and an Agno
structured-output call in real mode; the LLM can add tickers, but only
tickers in the covered universe survive.
"""
from __future__ import annotations

import re
from typing import Any

from src.copilot.entities import extract_fiscal_year, extract_metrics, extract_tickers, extract_weights, universe
from src.copilot.infra.metrics import record_llm_usage
from src.copilot.schemas import RouteDecision, ToolName
from src.copilot.state import CopilotState

ROUTER_MODEL = "gpt-4o-mini"
TOOL_ORDER: tuple[ToolName, ...] = ("fundamentals", "filing_search", "market_risk", "pretrade_check")

_RISK = re.compile(
    r"\b(var|value.at.risk|expected shortfall|tail risk|volatility|drawdown|portfolio risk|"
    r"risk of (my|the|this) portfolio|backtest|risk contribution)\b", re.I)
# Trade-specific phrases only: bare "concentration" or "limits" also occur in
# research questions ("supply chain concentration", "export limits").
_PRETRADE = re.compile(
    r"\b(pre.trade|(risk|position|concentration|trading) limits?|limit check|compliance check|"
    r"can (i|we) (buy|hold|allocate|put)|approve (this|the|my) (trade|portfolio|allocation)|"
    r"allowed to (buy|hold)|rebalanc\w*|proposed (portfolio|allocation|trade))\b", re.I)
_FILINGS = re.compile(
    r"\b(risk factors?|disclos\w*|mention\w*|say(s)? about|exposure to|regulat\w*|supply chain|competition|"
    r"litigation|lawsuits?|tariffs?|china|export controls?|cyber\w*|what risks|10-k says|warn\w*)\b", re.I)
_FUNDAMENTALS = re.compile(r"\b(financials|fundamentals|results|fiscal|reported|balance sheet|income statement)\b", re.I)


def route_mock(question: str, tickers: list[str], has_portfolio: bool) -> list[ToolName]:
    tools: set[ToolName] = set()
    if extract_metrics(question) or _FUNDAMENTALS.search(question):
        tools.add("fundamentals")
    if _FILINGS.search(question):
        tools.add("filing_search")
    if _PRETRADE.search(question):
        tools.add("pretrade_check")
    elif _RISK.search(question) or (has_portfolio and not tools):
        tools.add("market_risk")
    if not tools:
        tools.add("fundamentals" if tickers else "filing_search")
    return [t for t in TOOL_ORDER if t in tools]


def route_real(question: str, tickers: list[str]) -> RouteDecision:  # pragma: no cover - network
    from agno.agent import Agent
    from agno.models.openai import OpenAIChat

    instructions = f"""
You route questions for a financial research copilot covering these companies:
{", ".join(f"{t} ({c['display_name']})" for t, c in universe().items())}.

Choose every tool the question needs:
- fundamentals: reported financials from SEC XBRL (revenue, income, margins, EPS, debt, cash flow, growth)
- filing_search: what a company's 10-K risk factors say about a topic
- market_risk: Value at Risk, expected shortfall, volatility or risk attribution of a portfolio
- pretrade_check: whether a proposed portfolio or trade passes risk limits

Tickers already identified: {tickers}. Add any other covered company the
question names. Set fiscal_year only if the question names one.
"""
    agent = Agent(model=OpenAIChat(id=ROUTER_MODEL), instructions=instructions, output_schema=RouteDecision,
                  structured_outputs=True)
    run_output = agent.run(question)
    record_llm_usage("router", ROUTER_MODEL, run_output)
    content = run_output.content
    if isinstance(content, RouteDecision):
        return content
    if isinstance(content, dict):
        return RouteDecision(**content)
    raise TypeError(f"Unexpected router response: {type(content)!r}")


def router_node(state: CopilotState) -> dict[str, Any]:
    question = state["question"]
    tickers = list(dict.fromkeys([*state["tickers"], *extract_tickers(question)]))
    positions = state["positions"] or extract_weights(question)
    fiscal_year = extract_fiscal_year(question)

    if state["mock"]:
        tools = route_mock(question, tickers, bool(positions))
        reasoning = "keyword router"
    else:
        decision = route_real(question, tickers)
        tickers = list(dict.fromkeys([*tickers, *(t.upper() for t in decision.tickers)]))
        tools = [t for t in TOOL_ORDER if t in decision.tools] or ["filing_search"]
        fiscal_year = fiscal_year or decision.fiscal_year
        reasoning = decision.reasoning

    tickers = [t for t in tickers if t in universe()]
    if not positions and tickers and any(t in tools for t in ("market_risk", "pretrade_check")):
        positions = {t: round(1.0 / len(tickers), 6) for t in tickers}
    if "pretrade_check" in tools and "market_risk" in tools:
        tools.remove("market_risk")  # the pre-trade check already computes portfolio VaR

    return {
        "pending_tools": tools,
        "route_reasoning": reasoning,
        "tickers": tickers,
        "positions": positions,
        "fiscal_year": fiscal_year,
        "metrics": extract_metrics(question),
    }

"""
The orchestrator graph: START -> route -> {tool nodes}* -> synthesize -> END.

`pick_next` is a pure function over `pending_tools`, so every hop after
routing is deterministic Python, not another LLM decision. Tool nodes adapt
framework-agnostic tool functions (src/copilot/tools/) into state updates;
the tools themselves import nothing from LangGraph.

Heavy imports (DuckDB fact store, riskcore, price history, TF-IDF corpora)
are loaded by `warm_up()` at process start, not on the first request.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Hashable
from typing import Any

from langgraph.graph import END, START, StateGraph
from src.copilot.entities import DEFAULT_METRICS
from src.copilot.router import TOOL_ORDER, router_node
from src.copilot.state import CopilotState
from src.copilot.synthesis import synthesize_node
from src.copilot.tools.filing_search import run_filing_search
from src.copilot.tools.fundamentals import run_fundamentals
from src.copilot.tools.market_risk import run_market_risk
from src.copilot.tools.pretrade import run_pretrade_check
from src.copilot.tracing import NodeFn, traced_node
from src.filings.factstore import get_fact_store
from src.marketdata.prices import load_prices


def pick_next(state: CopilotState) -> str:
    return state["pending_tools"][0] if state["pending_tools"] else "synthesize"


def _tool_node(name: str, run: Callable[[CopilotState], dict[str, Any] | None]) -> NodeFn[CopilotState]:
    def node(state: CopilotState) -> dict[str, Any]:
        t0 = time.perf_counter()
        out = run(state)
        update: dict[str, Any] = {"pending_tools": state["pending_tools"][1:]}
        if out is None:
            update["gaps"] = [f"{name} needs at least one covered company; none was identified."]
            return update
        update.update({
            "evidence": [e.model_dump() for e in out["evidence"]],
            "findings": {name: out["findings"]},
            "gaps": out["gaps"],
            "tool_calls": [{
                "tool": name, "args": out.get("args", {}), "summary": out["summary"][:300],
                "duration_ms": round((time.perf_counter() - t0) * 1000, 2),
            }],
        })
        return update

    return node


def _fundamentals(state: CopilotState) -> dict[str, Any] | None:
    if not state["tickers"]:
        return None
    metrics = state["metrics"] or DEFAULT_METRICS
    out = run_fundamentals(get_fact_store(), state["tickers"], metrics, state["fiscal_year"], state["as_of"])
    out["args"] = {"tickers": state["tickers"], "metrics": metrics, "fiscal_year": state["fiscal_year"],
                   "as_of": state["as_of"]}
    return out


def _filing_search(state: CopilotState) -> dict[str, Any]:
    out = run_filing_search(state["question"], state["tickers"], mock=state["mock"])
    out["args"] = {"tickers": state["tickers"], "k": 3}
    return out


def _market_risk(state: CopilotState) -> dict[str, Any] | None:
    if not state["positions"]:
        return None
    out = run_market_risk(state["positions"], state["as_of"])
    out["args"] = {"positions": state["positions"], "as_of": state["as_of"]}
    return out


def _pretrade(state: CopilotState) -> dict[str, Any] | None:
    if not state["positions"]:
        return None
    out = run_pretrade_check(state["positions"], state["as_of"])
    out["args"] = {"positions": state["positions"], "as_of": state["as_of"]}
    return out


_NODES: dict[str, Callable[[CopilotState], dict[str, Any] | None]] = {
    "fundamentals": _fundamentals,
    "filing_search": _filing_search,
    "market_risk": _market_risk,
    "pretrade_check": _pretrade,
}


def warm_up() -> None:
    """Loads every lazily-built resource so startup, not the first user, pays for it."""
    get_fact_store()
    load_prices()
    run_filing_search("warm up", [], mock=True, k=1)


def build_graph() -> Any:
    """Stateless single-turn Q&A: no checkpointer. Every node is wrapped in an
    OTel span at construction time, keeping node functions tracing-agnostic."""
    graph = StateGraph(CopilotState)
    graph.add_node("route", traced_node("route", router_node))
    for name in TOOL_ORDER:
        graph.add_node(name, traced_node(name, _tool_node(name, _NODES[name])))
    graph.add_node("synthesize", traced_node("synthesize", synthesize_node))

    graph.add_edge(START, "route")
    path_map: dict[Hashable, str] = {**{n: n for n in TOOL_ORDER}, "synthesize": "synthesize"}
    graph.add_conditional_edges("route", pick_next, path_map)
    for name in TOOL_ORDER:
        graph.add_conditional_edges(name, pick_next, path_map)
    graph.add_edge("synthesize", END)
    return graph.compile()

"""
The orchestrator graph: START -> route -> {tool nodes}* -> synthesize -> END.

`route` computes the full ordered `pending_tools` list once; `pick_next` is
a pure function (no LLM call) that pops the front of that list, so every
hop after routing is deterministic Python, not another LLM decision. This
bounds real-mode LLM calls per request to at most 3 (route, synthesize, and
the complaint_classifier tool's own Agno call if it's in the route) no
matter how many specialist tools fire. See DECISIONS.md D26.

Node functions here adapt each pure tool function (src/copilot/tools/*.py)
into a CopilotState update — the tools themselves stay framework-agnostic
(no LangGraph/schema imports), only this module knows about graph state.
"""
from __future__ import annotations

from typing import Any

import pandas as pd
from langgraph.graph import END, START, StateGraph
from src.copilot.router import router_node
from src.copilot.state import CopilotState
from src.copilot.synthesis import synthesize_node

TOOL_NODES = ["data_analyst", "risk", "grounding", "complaint_classifier"]


def pick_next(state: CopilotState) -> str:
    """Pure conditional-edge function — no LLM call, no side effects."""
    return state["pending_tools"][0] if state["pending_tools"] else "synthesize"


def _pop(state: CopilotState) -> list[str]:
    return state["pending_tools"][1:]


def data_analyst_node(state: CopilotState) -> dict:
    from src.copilot.tools.data_analyst import (
        churn_rate_by_segment,
        get_clean_transactions,
        top_merchants_by_tpv,
        yoy_tpv_by_month,
    )

    df = get_clean_transactions()
    ref_date = df["reference_date"].max()
    start = (ref_date - pd.Timedelta(days=90)).strftime("%Y-%m-%d")
    end = ref_date.strftime("%Y-%m-%d")

    results: dict[str, Any] = {}
    tool_calls = []
    citations = []

    top = top_merchants_by_tpv(df, start, end, limit=5)
    results["top_merchants_by_tpv"] = top
    tool_calls.append({
        "tool": "data_analyst",
        "args": {"fn": "top_merchants_by_tpv", "start": start, "end": end},
        "summary": f"ranked {len(top)} merchant(s) by trailing-90d TPV",
    })
    if top:
        best = top[0]
        citations.append({
            "source_type": "kpi_query",
            "id": "top_merchants_by_tpv",
            "title": "Top merchants by TPV",
            "excerpt": f"merchant {best['merchant_id']}: TPV {best['tpv']:.2f}, approval rate {best['approval_rate']:.0%}",
        })

    # min_merchants=1 (not the function's own default of 20): this node
    # serves ad hoc copilot questions over whatever data is loaded (which
    # may be the small fixture), not a compliance-scale report — a
    # permissive floor still lets synthesis pick the most relevant segment.
    segments = churn_rate_by_segment(df, min_merchants=1)
    results["churn_rate_by_segment"] = segments
    tool_calls.append({
        "tool": "data_analyst",
        "args": {"fn": "churn_rate_by_segment", "min_merchants": 1},
        "summary": f"computed churn rate for {len(segments)} segment(s)",
    })
    if segments:
        worst = max(segments, key=lambda r: r["pct_churn"])
        citations.append({
            "source_type": "kpi_query",
            "id": "churn_rate_by_segment",
            "title": "Churn rate by segment",
            "excerpt": f"{worst['segment']}: {worst['pct_churn']:.0%} churn across {worst['n_merchants']} merchants",
        })

    if state["merchant_id"] is not None:
        yoy = yoy_tpv_by_month(df, state["merchant_id"])
        results["yoy_tpv_by_month"] = yoy
        tool_calls.append({
            "tool": "data_analyst",
            "args": {"fn": "yoy_tpv_by_month", "merchant_id": state["merchant_id"]},
            "summary": f"computed YoY TPV for merchant {state['merchant_id']} across {len(yoy)} month(s)",
        })
        declines = [r for r in yoy if r["tpv_yoy_pct"] is not None and r["tpv_yoy_pct"] < 0]
        if declines:
            worst = min(declines, key=lambda r: r["tpv_yoy_pct"])
            citations.append({
                "source_type": "kpi_query",
                "id": "yoy_tpv_by_month",
                "title": f"YoY TPV for merchant {state['merchant_id']}",
                "excerpt": f"{worst['month']}: TPV {worst['tpv_yoy_pct']:.0%} vs. same month last year",
            })

    return {
        "tool_results": {"data_analyst": results},
        "tool_calls": tool_calls,
        "citations": citations,
        "pending_tools": _pop(state),
    }


def risk_node(state: CopilotState) -> dict:
    from src.copilot.tools.data_analyst import get_clean_transactions
    from src.copilot.tools.risk import score_merchant
    from src.parte1_pandas import merchants_at_risk

    df = get_clean_transactions()

    if state["merchant_id"] is not None:
        candidate_ids = [state["merchant_id"]]
        via_heuristic = False
    else:
        # No specific merchant named — use the existing TPV/approval/
        # complaint heuristic (D3) to pick candidates worth scoring against
        # the ML model, rather than scoring every merchant in the dataset.
        at_risk = merchants_at_risk(df, top_n=3)
        candidate_ids = at_risk["merchant_id"].tolist()
        via_heuristic = True

    scores = [score_merchant(mid, df=df) for mid in candidate_ids]

    tool_calls = [{
        "tool": "risk",
        "args": {"merchant_ids": candidate_ids, "via_heuristic_shortlist": via_heuristic},
        "summary": f"scored {len(scores)} merchant(s) against the churn model",
    }]
    citations = []
    for s in scores:
        if s["found"]:
            citations.append({
                "source_type": "model_output",
                "id": f"churn_model:{s['merchant_id']}",
                "title": f"Churn score for merchant {s['merchant_id']}",
                "excerpt": f"{s['risk_tier']} risk ({s['churn_probability']:.0%}). {s['caveat']}"[:400],
            })

    return {
        "tool_results": {"risk": {"scores": scores, "via_heuristic_shortlist": via_heuristic}},
        "tool_calls": tool_calls,
        "citations": citations,
        "pending_tools": _pop(state),
    }


def grounding_node(state: CopilotState) -> dict:
    from src.copilot.tools.grounding import retrieve_policy

    docs = retrieve_policy(state["question"], k=3, mock=state["mock"])
    citations = [
        {"source_type": "policy_doc", "id": d["id"], "title": d["title"], "excerpt": d["text"][:400]}
        for d in docs
    ]
    tool_calls = [{
        "tool": "grounding",
        "args": {"k": 3},
        "summary": f"retrieved {len(docs)} policy doc(s)",
    }]
    return {
        "tool_results": {"grounding": docs},
        "tool_calls": tool_calls,
        "citations": citations,
        "pending_tools": _pop(state),
    }


def complaint_classifier_node(state: CopilotState) -> dict:
    from src.copilot.tools.complaint_classifier import classify_complaint

    result = classify_complaint(state["merchant_id"] or 0, state["question"], locale=state["locale"])
    tool_calls = [{
        "tool": "complaint_classifier",
        "args": {"merchant_id": state["merchant_id"]},
        "summary": f"classified as {result['category']} (urgency {result['urgency']})",
    }]
    citations = []
    if result.get("similar_cases_used"):
        citations.append({
            "source_type": "historical_case",
            "id": "similar_complaint_cases",
            "title": "Similar historical complaints",
            "excerpt": f"Retrieved for calibration; classified category={result['category']}",
        })
    return {
        "tool_results": {"complaint_classifier": result},
        "tool_calls": tool_calls,
        "citations": citations,
        "pending_tools": _pop(state),
    }


_NODE_FNS = {
    "data_analyst": data_analyst_node,
    "risk": risk_node,
    "grounding": grounding_node,
    "complaint_classifier": complaint_classifier_node,
}


def build_graph():
    """Compiles the orchestrator graph. No checkpointer: /ask is stateless
    single-turn Q&A in this version — LangGraph's persistence layer would be
    unjustified complexity for that (see DECISIONS.md D26).
    """
    graph = StateGraph(CopilotState)
    graph.add_node("route", router_node)
    for name, fn in _NODE_FNS.items():
        graph.add_node(name, fn)
    graph.add_node("synthesize", synthesize_node)

    graph.add_edge(START, "route")
    path_map = {**{name: name for name in TOOL_NODES}, "synthesize": "synthesize"}
    graph.add_conditional_edges("route", pick_next, path_map)
    for name in TOOL_NODES:
        graph.add_conditional_edges(name, pick_next, path_map)
    graph.add_edge("synthesize", END)

    return graph.compile()

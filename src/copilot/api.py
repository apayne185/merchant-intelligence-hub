"""
FastAPI app for the Merchant Intelligence Copilot.

Endpoints:
  - GET  /health
  - POST /ask

Runs independently of src/parte4_api/main.py — the complaint-classifier
service keeps running standalone on its own port; this is the new flagship
entry point, not a replacement mounted into the same app (see DECISIONS.md
D27 for why not).

Starts with:
    export MOCK_LLM=1                 # or export OPENAI_API_KEY=...
    uvicorn src.copilot.api:app --reload --port 8001
"""
from __future__ import annotations

import logging
import os
import time
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from src.copilot.graph import build_graph
from src.copilot.schemas import AskRequest, AskResponse
from src.copilot.state import initial_state
from src.parte4_api.agent import is_mock_mode

# Reused as-is (D22/D25's "share, don't duplicate" reasoning): the health
# contract (status/model/version) is identical to src/parte4_api's.
from src.parte4_api.schemas import HealthResponse

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Merchant Intelligence Copilot",
    version="0.1.0",
    description=(
        "Multi-agent orchestrator answering merchant questions via KPI/SQL "
        "tools, a churn-risk model, and policy RAG, with cited answers."
    ),
)


def get_graph():
    """Factory for the compiled graph. Cheap to (re)compile — the tool
    modules themselves cache their own expensive state (dataframes, models,
    vector stores), not the graph object. Tests override this via
    app.dependency_overrides, same pattern as src/parte4_api/main.py's
    get_agent()/AgentDep.
    """
    return build_graph()


GraphDep = Annotated[object, Depends(get_graph)]


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """`status="degraded"` if no MOCK_LLM nor OPENAI_API_KEY is configured —
    every /ask request that needs the real router/synthesizer would fail,
    even though the process is alive. Mirrors src/parte4_api/main.py:health().
    """
    if is_mock_mode():
        return HealthResponse(status="ok", model="mock", version=app.version)
    if os.environ.get("OPENAI_API_KEY"):
        return HealthResponse(status="ok", model="gpt-4o-mini", version=app.version)
    return HealthResponse(status="degraded", model="unconfigured", version=app.version)


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest, graph: GraphDep) -> AskResponse:
    """Answers a natural-language merchant question by routing it through
    the orchestrator graph."""
    t0 = time.perf_counter()
    mock = is_mock_mode()

    state = initial_state(req.question, merchant_id=req.merchant_id, locale=req.locale, mock=mock)
    try:
        result = graph.invoke(state)
    except Exception:
        # No exponer str(exc) al cliente — same reasoning as /classify:
        # could leak request URLs, model config, or SDK stack traces.
        logger.exception("copilot /ask failed for question=%r", req.question)
        raise HTTPException(status_code=502, detail="copilot_error") from None

    # Distinct tools that actually fired, in first-occurrence order — not
    # the raw tool_calls list, which can have repeats (data_analyst logs
    # one entry per underlying SQL query it ran).
    route = list(dict.fromkeys(tc["tool"] for tc in result["tool_calls"]))

    return AskResponse(
        question=req.question,
        route=route,
        answer=result["answer"] or "No information was found for this question.",
        citations=result["citations"],
        tool_calls=result["tool_calls"],
        mode="mock" if mock else "real",
        latency_ms=int((time.perf_counter() - t0) * 1000),
    )


# -----------------------------------------------------------------------------
# Sanity smoke (manual): `python -m src.copilot.api`
# -----------------------------------------------------------------------------
if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)

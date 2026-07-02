"""
FastAPI app del servicio de clasificación de reclamaciones.

Endpoints obligatorios:
  - GET  /health
  - POST /classify
  - POST /classify/batch

Arranca con:
    export MOCK_LLM=1                 # o export OPENAI_API_KEY=...
    uvicorn src.parte4_api.main:app --reload --port 8000
"""
from __future__ import annotations

import asyncio
import time
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException

from .agent import build_agent, detect_prompt_injection, redact_pii
from .schemas import (
    BatchClassifyRequest,
    BatchClassifyResponse,
    Category,
    ClassifyRequest,
    ClassifyResponse,
    HealthResponse,
)

# -----------------------------------------------------------------------------
# App
# -----------------------------------------------------------------------------
app = FastAPI(
    title="Merchant Intelligence Hub — Complaint Classifier",
    version="0.1.0",
    description="Agno agent behind FastAPI for merchant complaint triage",
)


# -----------------------------------------------------------------------------
# Dependency: agente (mockeable en tests vía app.dependency_overrides)
# -----------------------------------------------------------------------------
def get_agent():
    """
    Factory del agente. Cambia esto si quieres caché global, pool de clientes,
    etc. En tests, sustituye con `app.dependency_overrides[get_agent] = ...`.
    """
    return build_agent()


AgentDep = Annotated[object, Depends(get_agent)]


# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------
@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness probe. Devuelve modelo activo y versión.
    """
    # TODO: rellena 'model' con el modelo realmente configurado
    return HealthResponse(status="ok", model="gpt-4o-mini-or-mock", version=app.version)


@app.post("/classify", response_model=ClassifyResponse)
def classify(req: ClassifyRequest, agent: AgentDep) -> ClassifyResponse:
    """Clasifica una reclamación individual.
    """
    t0 = time.perf_counter()

    # 1. Guardrail prompt injection (defensa antes del LLM)
    if detect_prompt_injection(req.email_text):
        return ClassifyResponse(
            merchant_id=req.merchant_id,
            category=Category.other,
            urgency=1,
            requires_human_escalation=True,
            reasoning="prompt_injection_detected",
            merchant_context_used=False,
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )

    # 2. PII redaction antes de pasar al LLM
    safe_text = redact_pii(req.email_text)

    # 3. Llamada al agente. El _MockAgent expone `.classify(...)`;
    #    si construyes un Agent de Agno real, adapta esta llamada para que
    #    devuelva un dict (o un objeto Pydantic) con los mismos campos.
    try:
        result = agent.classify(
            merchant_id=req.merchant_id,
            email_text=safe_text,
            locale=req.locale,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"agent_error: {exc}") from exc

    return ClassifyResponse(
        merchant_id=result["merchant_id"],
        category=result["category"],
        urgency=result["urgency"],
        requires_human_escalation=result["requires_human_escalation"],
        reasoning=result["reasoning"][:300],
        merchant_context_used=result.get("merchant_context_used", False),
        latency_ms=int((time.perf_counter() - t0) * 1000),
    )


@app.post("/classify/batch", response_model=BatchClassifyResponse)
async def classify_batch(req: BatchClassifyRequest, agent: AgentDep) -> BatchClassifyResponse:
    """Procesa hasta 50 reclamaciones concurrentemente.
    """
    t0 = time.perf_counter()

    async def _one(item: ClassifyRequest) -> ClassifyResponse | None:
        try:
            # Empujamos la llamada sync a thread para no bloquear el loop
            return await asyncio.to_thread(classify, item, agent)
        except Exception:
            return None

    results = await asyncio.gather(*[_one(it) for it in req.items])
    ok = [r for r in results if r is not None]
    n_failed = len(results) - len(ok)

    return BatchClassifyResponse(
        results=ok,
        total_latency_ms=int((time.perf_counter() - t0) * 1000),
        n_failed=n_failed,
    )


# -----------------------------------------------------------------------------
# Sanity smoke (manual): `python -m src.parte4_api.main`
# -----------------------------------------------------------------------------
if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

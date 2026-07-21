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
import logging
import os
import time
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException

from .agent import build_agent, detect_prompt_injection, is_mock_mode, redact_pii
from .schemas import (
    BatchClassifyRequest,
    BatchClassifyResponse,
    BatchErrorItem,
    BatchResultItem,
    Category,
    ClassifyRequest,
    ClassifyResponse,
    HealthResponse,
)

logger = logging.getLogger(__name__)

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

    `status="degraded"` si no hay MOCK_LLM ni una OPENAI_API_KEY configurada —
    en ese caso todo /classify request va a 502 aunque el proceso esté vivo.
    """
    if is_mock_mode():
        return HealthResponse(status="ok", model="mock", version=app.version)

    if os.environ.get("OPENAI_API_KEY"):
        return HealthResponse(status="ok", model="gpt-4o-mini", version=app.version)

    return HealthResponse(status="degraded", model="unconfigured", version=app.version)


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
            similar_cases_used=False,
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )

    # 2. PII redaction antes de pasar al LLM
    safe_text = redact_pii(req.email_text)

    # 3. Llamada al agente. Tanto _MockAgent como _RealAgentAdapter (agent.py)
    #    exponen `.classify(...)` y devuelven el mismo dict de campos.
    try:
        result = agent.classify(
            merchant_id=req.merchant_id,
            email_text=safe_text,
            locale=req.locale,
        )
    except Exception:
        # No exponer str(exc) al cliente: puede filtrar detalles internos
        # (URLs de request, config del modelo, trazas del SDK).
        logger.exception("classify failed for merchant_id=%s", req.merchant_id)
        raise HTTPException(status_code=502, detail="agent_error") from None

    # PII redaction también a la salida: el LLM puede repetir en `reasoning`
    # datos de contexto del merchant que sí contienen PII real (no solo el
    # email de entrada, ya redactado en el paso 2).
    reasoning = redact_pii(result["reasoning"])[:300]

    return ClassifyResponse(
        merchant_id=result["merchant_id"],
        category=result["category"],
        urgency=result["urgency"],
        requires_human_escalation=result["requires_human_escalation"],
        reasoning=reasoning,
        merchant_context_used=result.get("merchant_context_used", False),
        similar_cases_used=result.get("similar_cases_used", False),
        latency_ms=int((time.perf_counter() - t0) * 1000),
    )


@app.post("/classify/batch", response_model=BatchClassifyResponse)
async def classify_batch(req: BatchClassifyRequest, agent: AgentDep) -> BatchClassifyResponse:
    """Procesa hasta 50 reclamaciones concurrentemente.

    Cada resultado/error lleva el índice del item original en `req.items`,
    para que el caller pueda correlacionar la respuesta con su request —
    varios items pueden compartir merchant_id, así que el índice es la
    única clave fiable.
    """
    t0 = time.perf_counter()

    async def _one(index: int, item: ClassifyRequest) -> tuple[int, ClassifyResponse | None]:
        try:
            # Empujamos la llamada sync a thread para no bloquear el loop
            resp = await asyncio.to_thread(classify, item, agent)
            return index, resp
        except Exception:
            logger.exception("classify_batch item %d failed (merchant_id=%s)", index, item.merchant_id)
            return index, None

    outcomes = await asyncio.gather(*[_one(i, item) for i, item in enumerate(req.items)])

    results = [BatchResultItem(index=i, response=r) for i, r in outcomes if r is not None]
    errors = [
        BatchErrorItem(index=i, merchant_id=req.items[i].merchant_id, message="agent_error")
        for i, r in outcomes
        if r is None
    ]

    return BatchClassifyResponse(
        results=results,
        errors=errors,
        total_latency_ms=int((time.perf_counter() - t0) * 1000),
        n_failed=len(errors),
    )


# -----------------------------------------------------------------------------
# Sanity smoke (manual): `python -m src.parte4_api.main`
# -----------------------------------------------------------------------------
if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

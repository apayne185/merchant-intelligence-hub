"""
Pydantic v2 schemas para la API de clasificación de reclamaciones.

Estos schemas son el contrato del endpoint. Si los tocas, asegúrate de que tu
agente Agno devuelve datos compatibles y de que los tests siguen pasando.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, conint


# -----------------------------------------------------------------------------
# Enums
# -----------------------------------------------------------------------------
class Category(str, Enum):
    technical_issue = "technical_issue"
    billing = "billing"
    onboarding = "onboarding"
    fraud = "fraud"
    churn_threat = "churn_threat"
    other = "other"


# -----------------------------------------------------------------------------
# Request
# -----------------------------------------------------------------------------
class ClassifyRequest(BaseModel):
    """Reclamación de un merchant a clasificar.
    """

    merchant_id: int = Field(..., description="ID del merchant que envía la reclamación / ID of the merchant sending the complaint")
    email_text: str = Field(..., min_length=1, description="Texto íntegro del email / Full text of the email")
    locale: Literal["es", "pt", "en"] = Field(default="es", description="Idioma del email / Language of the email")


class BatchClassifyRequest(BaseModel):
    """Lote de reclamaciones (hasta 50 por petición).
    """

    items: list[ClassifyRequest] = Field(..., max_length=50, min_length=1)


# -----------------------------------------------------------------------------
# Response
# -----------------------------------------------------------------------------
class ClassifyResponse(BaseModel):
    """Resultado de clasificación de una reclamación.
    """

    merchant_id: int
    category: Category
    urgency: conint(ge=1, le=5)  # type: ignore[valid-type]
    requires_human_escalation: bool
    reasoning: str = Field(..., max_length=300)
    merchant_context_used: bool = Field(
        ..., description="True si el agente invocó la tool de contexto del merchant / True if the agent invoked the merchant context tool"
    )
    latency_ms: int


class BatchResultItem(BaseModel):
    """Un resultado de batch, con el índice del item original para poder
    correlacionar la respuesta con `BatchClassifyRequest.items`.
    """

    index: int
    response: ClassifyResponse


class BatchErrorItem(BaseModel):
    """Un fallo de batch, con el índice del item original."""

    index: int
    merchant_id: int
    message: str


class BatchClassifyResponse(BaseModel):
    results: list[BatchResultItem]
    errors: list[BatchErrorItem] = Field(default_factory=list)
    total_latency_ms: int
    n_failed: int


# -----------------------------------------------------------------------------
# Health
# -----------------------------------------------------------------------------
class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    model: str
    version: str

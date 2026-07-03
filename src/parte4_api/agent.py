"""
Agente Agno para clasificar reclamaciones de merchants.

Expone `build_agent()`, que devuelve `_MockAgent` (determinístico, sin
llamadas a OpenAI) si `MOCK_LLM` está activo, o un `Agent` de Agno real
envuelto en `_RealAgentAdapter` en caso contrario — ambos exponen la misma
interfaz `.classify(merchant_id, email_text, locale)`.

Incluye 2 tools custom (`get_merchant_context`, `flag_for_human_review`),
guardrail de prompt injection, y PII redaction antes del LLM (ver
`src/parte4_api/README.md` "Guardrails implementados").

Docs Agno: https://docs.agno.com
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

# -----------------------------------------------------------------------------
# Constantes y helpers
# -----------------------------------------------------------------------------
# Repo root = parents[2] desde agent.py (.../repo/src/parte4_api/agent.py).
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
OUTPUTS_DIR = REPO_ROOT / "outputs"
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

PROMPT_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    # English
    re.compile(r"ignore (?:all )?previous instructions", re.IGNORECASE),
    re.compile(r"\bsystem\s*:", re.IGNORECASE),
    re.compile(r"\bdisregard\b.*\b(prompt|instructions)\b", re.IGNORECASE),
    # Español — el locale primario documentado de la API, sin cobertura hasta ahora
    re.compile(r"ignora(?:r)?\s+(?:todas\s+)?las\s+instrucciones\s+anteriores", re.IGNORECASE),
    re.compile(r"\bignora\b.*\b(prompt|instrucciones)\b", re.IGNORECASE),
    # Português
    re.compile(r"ignor[ae]\s+(?:todas\s+)?as\s+instru[çc][õo]es\s+anteriores", re.IGNORECASE),
    # TODO: añade más patrones que consideres relevantes
]

# Orden importante: más específico (card) → menos específico (phone) → email.
# Si phone se aplicase antes que card, capturaría 16 dígitos con espacios.
PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "card": re.compile(r"\b(?:\d[ -]?){12,18}\d\b"),
    "phone": re.compile(r"\+?\d[\d\s\-]{7,11}\d"),
    "email": re.compile(r"[\w\.-]+@[\w\.-]+\.\w+"),
    # TODO: añade más si lo consideras (CPF, CNPJ, IBAN…)
}


def is_mock_mode() -> bool:
    """True si debe usarse el LLM stub (no llama a OpenAI).
    """
    return os.environ.get("MOCK_LLM", "").lower() in {"1", "true", "yes"}


def detect_prompt_injection(text: str) -> bool:
    """Devuelve True si `text` contiene patrones típicos de prompt injection.
    """
    return any(p.search(text) for p in PROMPT_INJECTION_PATTERNS)


def redact_pii(text: str) -> str:
    """Reemplaza PII (email/teléfono/tarjeta) por placeholders [EMAIL]/[PHONE]/[CARD].
    """
    redacted = text
    for label, pattern in PII_PATTERNS.items():
        redacted = pattern.sub(f"[{label.upper()}]", redacted)
    return redacted


# -----------------------------------------------------------------------------
# Tools del agente
# -----------------------------------------------------------------------------
_MERCHANTS_CACHE: dict[int, dict[str, Any]] | None = None


def _load_merchants() -> dict[int, dict[str, Any]]:
    global _MERCHANTS_CACHE
    if _MERCHANTS_CACHE is None:
        path = DATA_DIR / "merchants_context.json"
        if not path.exists():
            _MERCHANTS_CACHE = {}
        else:
            data = json.loads(path.read_text())
            _MERCHANTS_CACHE = {m["merchant_id"]: m for m in data}
    return _MERCHANTS_CACHE


def get_merchant_context(merchant_id: int) -> dict[str, Any]:
    """
    Tool del agente: devuelve contexto del merchant.

    Si el merchant_id no existe, decide qué hacer y documéntalo en DECISIONS.md.
    """
    merchants = _load_merchants()
    return merchants.get(merchant_id, {"merchant_id": merchant_id, "found": False})


def flag_for_human_review(merchant_id: int, reason: str) -> dict[str, Any]:
    """
    Tool del agente: registra el caso en outputs/human_review_queue.jsonl.

    Side-effect real, no mock. Appendea una línea JSON por llamada.
    """
    queue_path = OUTPUTS_DIR / "human_review_queue.jsonl"
    record = {"merchant_id": merchant_id, "reason": reason}
    with queue_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return {"queued": True, "merchant_id": merchant_id}


# -----------------------------------------------------------------------------
# Agente Agno real
# -----------------------------------------------------------------------------
from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.tools import tool as agno_tool
from pydantic import BaseModel, Field, conint

from .schemas import Category

_AGENT_INSTRUCTIONS = """
Eres un clasificador de reclamaciones de merchants para un adquirente de pagos.

Dado un email, debes:
1. Llamar a merchant_context_tool con merchant_id para obtener contexto del merchant.
2. Clasificar en una de: technical_issue, billing, onboarding, fraud, churn_threat, other.
3. Asignar urgency 1-5 (5=crítico: bloqueo total o amenaza de cancelación inmediata).
4. Si urgency >= 4 o category es churn_threat/fraud: requires_human_escalation=True
   y llamar a flag_human_review_tool con el motivo.
5. Escribir reasoning conciso (≤ 300 chars).
Responde SIEMPRE en el schema estructurado.
"""


@agno_tool
def merchant_context_tool(merchant_id: int) -> dict:
    """Obtiene segmento, TPV y quejas recientes del merchant.
    """
    return get_merchant_context(merchant_id)


@agno_tool
def flag_human_review_tool(merchant_id: int, reason: str) -> dict:
    """Registra el caso en la cola de revisión humana.
    """
    return flag_for_human_review(merchant_id, reason)


class _LLMClassification(BaseModel):
    """response_model para el Agent real de Agno.

    Igual que `ClassifyResponse` pero sin `merchant_id`/`latency_ms`: esos los
    conoce el caller (request + reloj), no tiene sentido que el LLM los genere.
    """

    category: Category
    urgency: conint(ge=1, le=5)  # type: ignore[valid-type]
    requires_human_escalation: bool
    reasoning: str = Field(..., max_length=300)
    merchant_context_used: bool


class _RealAgentAdapter:
    """Envuelve un `Agent` de Agno real para exponer la misma interfaz
    `.classify()` que `_MockAgent`, así `main.py` no necesita saber cuál de
    los dos está usando.
    """

    def __init__(self, agent: Agent) -> None:
        self._agent = agent

    def classify(self, *, merchant_id: int, email_text: str, locale: str = "es") -> dict[str, Any]:
        prompt = f"merchant_id: {merchant_id}\nlocale: {locale}\n\n{email_text}"
        run_output = self._agent.run(prompt)
        content = run_output.content
        if isinstance(content, _LLMClassification):
            data = content.model_dump()
        elif isinstance(content, dict):
            data = dict(content)
        else:
            raise TypeError(f"Respuesta inesperada del agente Agno: {type(content)!r}")
        data["merchant_id"] = merchant_id
        return data


def build_agent(model_name: str = "gpt-4o-mini"):
    """Construye el agente. Si MOCK_LLM=1, devuelve el stub determinístico;
    si no, un Agent de Agno real envuelto en `_RealAgentAdapter`.
    """
    if is_mock_mode():
        return _MockAgent()

    real_agent = Agent(
        model=OpenAIChat(id=model_name),
        tools=[merchant_context_tool, flag_human_review_tool],
        instructions=_AGENT_INSTRUCTIONS,
        output_schema=_LLMClassification,
        structured_outputs=True,
    )
    return _RealAgentAdapter(real_agent)


class _MockAgent:
    """
    Stub determinístico para tests offline. NO es la solución final.

    Reglas mínimas:
      - Si el texto detecta prompt injection → category=other, urgency=1.
      - Si menciona 'cancelar' o 'churn' → category=churn_threat, urgency=4.
      - En otro caso → category=other, urgency=2.
    """

    def classify(self, *, merchant_id: int, email_text: str, locale: str = "es") -> dict[str, Any]:
        if detect_prompt_injection(email_text):
            return {
                "merchant_id": merchant_id,
                "category": "other",
                "urgency": 1,
                "requires_human_escalation": True,
                "reasoning": "prompt_injection_detected",
                "merchant_context_used": False,
            }
        ctx = get_merchant_context(merchant_id)
        if any(kw in email_text.lower() for kw in ["cancelar", "cancel", "churn"]):
            return {
                "merchant_id": merchant_id,
                "category": "churn_threat",
                "urgency": 4,
                "requires_human_escalation": True,
                "reasoning": "menciona intención de cancelar",
                "merchant_context_used": bool(ctx.get("found", True) is not False),
            }
        return {
            "merchant_id": merchant_id,
            "category": "other",
            "urgency": 2,
            "requires_human_escalation": False,
            "reasoning": "stub default",
            "merchant_context_used": False,
        }

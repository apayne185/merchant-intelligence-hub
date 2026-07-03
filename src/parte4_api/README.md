# Parte 4 · API de clasificación de reclamaciones — `src/parte4_api/`
*[EN]: Part 4 · Complaint classification API — `src/parte4_api/`*

FastAPI service that classifies inbound merchant complaint emails via an Agno
agent, with prompt-injection and PII guardrails in front of the LLM call.

## Arquitectura
*[EN]: Architecture*

```
parte4_api/
├── main.py        ← FastAPI app + endpoints + dependency injection
├── agent.py       ← Agno agent (mock + real) + tools + guardrails + PII redaction
├── schemas.py     ← Pydantic v2 models (request/response)
└── README.md      ← este archivo / this file
```

## Cómo arrancar
*[EN]: How to start*

```bash
# Opción A · con clave OpenAI propia
# [EN]: Option A · with your own OpenAI key
export OPENAI_API_KEY=sk-...
uvicorn src.parte4_api.main:app --reload --port 8000

# Opción B · sin clave (LLM mockeado, determinístico)
# [EN]: Option B · without key (mocked LLM, deterministic)
export MOCK_LLM=1
uvicorn src.parte4_api.main:app --reload --port 8000
```

## Endpoints

### `GET /health`

```bash
curl -s http://localhost:8000/health | jq
```

Respuesta (`status` es `"degraded"` si no hay `MOCK_LLM` ni `OPENAI_API_KEY` configurados):
*[EN]: Response (`status` is `"degraded"` if neither `MOCK_LLM` nor `OPENAI_API_KEY` is set):*
```json
{ "status": "ok", "model": "gpt-4o-mini", "version": "0.1.0" }
```

### `POST /classify`

```bash
curl -s -X POST http://localhost:8000/classify \
  -H 'Content-Type: application/json' \
  -d '{
    "merchant_id": 10063716,
    "email_text": "Llevo 3 días sin poder cobrar con el POS, voy a cancelar la cuenta.",
    "locale": "es"
  }' | jq
```

Respuesta:
*[EN]: Response:*
```json
{
  "merchant_id": 10063716,
  "category": "churn_threat",
  "urgency": 4,
  "requires_human_escalation": true,
  "reasoning": "...",
  "merchant_context_used": true,
  "latency_ms": 423
}
```

### `POST /classify/batch`

Hasta 50 items por request, procesados concurrentemente. Cada resultado/error
lleva el `index` del item original en la request, para poder correlacionarlos
(varios items pueden compartir `merchant_id`).
*[EN]: Up to 50 items per request, processed concurrently. Each result/error
carries the original request item's `index`, so callers can correlate them
(multiple items can share the same `merchant_id`).*

```bash
curl -s -X POST http://localhost:8000/classify/batch \
  -H 'Content-Type: application/json' \
  -d '{"items":[
    {"merchant_id":10063716,"email_text":"POS roto","locale":"es"},
    {"merchant_id":10063717,"email_text":"Factura incorrecta","locale":"es"}
  ]}' | jq
```

## Tests

```bash
MOCK_LLM=1 pytest -v tests/test_api.py tests/test_agent_adapter.py
```

`test_api.py` covers `/health`, `/classify` (happy path, es/en prompt-injection
guardrail, invalid input), and `/classify/batch` concurrency —
all run against `_MockAgent` (`MOCK_LLM=1`, no OpenAI dependency).
`test_agent_adapter.py` covers the real-Agno-agent adapter (`_RealAgentAdapter`)
by mocking `Agent.run`, so it doesn't need a real `OPENAI_API_KEY` either — it
verifies the adapter constructs correctly and maps Agno's `RunOutput` into the
same dict shape `_MockAgent` returns, but does **not** exercise an actual
OpenAI call end-to-end (see `SELF_REVIEW.md` P3).

## Decisiones técnicas
*[EN]: Technical decisions*

Ver `DECISIONS.md`, sección "Parte 4 · FastAPI + Agno", para: por qué Agno,
modelo elegido y estimación de coste, trade-offs del schema Pydantic, y cómo
se evaluaría la calidad antes de producción.
*[EN]: See `DECISIONS.md`, "Parte 4 · FastAPI + Agno" section, for: why Agno,
chosen model and cost estimate, Pydantic schema trade-offs, and how quality
would be evaluated before production.*

## Guardrails implementados
*[EN]: Implemented guardrails*

- **Prompt injection**: patrones regex en `agent.detect_prompt_injection`, en
  español, portugués e inglés. Heurístico, no una defensa robusta — bypasses
  triviales (unicode homoglyphs, texto codificado, frases partidas en líneas)
  no están cubiertos. Ver "Limitaciones conocidas" abajo.
*[EN]: - Prompt injection: regex patterns in `agent.detect_prompt_injection`,
  in Spanish, Portuguese and English. Heuristic, not a robust defense —
  trivial bypasses (unicode homoglyphs, encoded text, phrases split across
  lines) aren't covered. See "Known limitations" below.*
- **PII redaction**: `agent.redact_pii` redacta email/teléfono/tarjeta tanto
  en el texto que se manda al LLM como en el `reasoning` que devuelve (el
  LLM puede repetir PII del contexto del merchant en su respuesta).
*[EN]: - PII redaction: `agent.redact_pii` redacts email/phone/card both in
  the text sent to the LLM and in the `reasoning` it returns (the LLM can
  echo merchant-context PII back in its response).*
- **Escalado humano**: tool `flag_for_human_review` persiste en
  `outputs/human_review_queue.jsonl`.
*[EN]: - Human escalation: `flag_for_human_review` tool persists to
  `outputs/human_review_queue.jsonl`.*

## Limitaciones conocidas
*[EN]: Known limitations*

- **Sin autenticación ni rate limiting**: `/classify` y `/classify/batch` no
  requieren API key ni limitan requests por cliente. Fuera de scope para este
  proyecto, pero sería necesario antes de exponer esto públicamente.
*[EN]: - No auth or rate limiting: `/classify` and `/classify/batch` require
  no API key and don't limit requests per client. Out of scope for this
  project, but would be required before exposing this publicly.*
- **Guardrail de prompt injection es heurístico**: ver nota arriba — cubre
  frases comunes en 3 idiomas, no es una defensa robusta contra un atacante
  motivado.
*[EN]: - Prompt-injection guardrail is heuristic: see note above — covers
  common phrasing in 3 languages, not a robust defense against a motivated
  attacker.*
- **Ruta real (no-mock) no probada end-to-end**: sin `OPENAI_API_KEY`
  disponible, el adaptador del Agent real (`_RealAgentAdapter`) está
  verificado por construcción e interfaz (test con `Agent.run` mockeado),
  pero no contra una respuesta real de OpenAI. Ver `SELF_REVIEW.md` P3.
*[EN]: - Real (non-mock) path not tested end-to-end: without an
  `OPENAI_API_KEY` available, the real Agent adapter (`_RealAgentAdapter`) is
  verified for construction and interface (test with a mocked `Agent.run`),
  but not against a real OpenAI response. See `SELF_REVIEW.md` P3.*
- **`flag_for_human_review` no es thread-safe**: escribe a
  `outputs/human_review_queue.jsonl` con un `open(...).write()` simple, sin
  lock — requests concurrentes de `/classify/batch` podrían intercalar
  escrituras parciales bajo carga real.
*[EN]: - `flag_for_human_review` isn't thread-safe: writes to
  `outputs/human_review_queue.jsonl` with a plain `open(...).write()`, no
  lock — concurrent `/classify/batch` requests could interleave partial
  writes under real load.*

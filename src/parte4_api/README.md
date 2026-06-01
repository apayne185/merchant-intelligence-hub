# Parte 4 · API de clasificación de reclamaciones — `src/parte4_api/`
*[EN]: Part 4 · Complaint classification API — `src/parte4_api/`*

> Este README es **tu** entregable: cuando termines, descríbelo desde la perspectiva del evaluador. Reemplaza las secciones que necesites con tu implementación real.
> *[EN]: This README is your deliverable: when you are done, describe it from the evaluator's perspective. Replace the sections you need with your real implementation.*

## Arquitectura
*[EN]: Architecture*

```
parte4_api/
├── main.py        ← FastAPI app + endpoints + dependency injection
├── agent.py       ← agente Agno + tools + guardrails + PII redaction
                      [EN]: Agno agent + tools + guardrails + PII redaction
├── schemas.py     ← Pydantic v2 models (request/response)
└── README.md      ← este archivo / this file
```

## Cómo arrancar (el evaluador ejecutará exactamente esto)
*[EN]: How to start (the evaluator will run exactly this)*

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

Respuesta:
*[EN]: Response:*
```json
{ "status": "ok", "model": "gpt-4o-mini-or-mock", "version": "0.1.0" }
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
MOCK_LLM=1 pytest -v tests/test_api.py
```

Cobertura mínima: `test_health`, `test_classify_happy_path`, `test_classify_prompt_injection`, `test_classify_invalid_input`, `test_batch_concurrency`.
*[EN]: Minimum coverage: `test_health`, `test_classify_happy_path`, `test_classify_prompt_injection`, `test_classify_invalid_input`, `test_batch_concurrency`.*

## Decisiones técnicas
*[EN]: Technical decisions*

Documenta en `DECISIONS.md`:
*[EN]: Document in `DECISIONS.md`:*

- Por qué Agno (vs LangChain/LlamaIndex).
*[EN]: - Why Agno (vs LangChain/LlamaIndex).*
- Modelo elegido + estimación de coste mensual procesando 5.000 emails/día.
*[EN]: - Chosen model + monthly cost estimate processing 5,000 emails/day.*
- Trade-offs del schema Pydantic (¿por qué enum cerrado? ¿por qué cap 300 chars en `reasoning`?).
*[EN]: - Pydantic schema trade-offs (why a closed enum? why cap 300 chars in `reasoning`?).*
- Cómo evaluarías la calidad antes de producción (golden set, LLM-as-judge, métricas).
*[EN]: - How you would evaluate quality before production (golden set, LLM-as-judge, metrics).*
- Mitigación cuando el LLM clasifica mal una urgencia 5 como urgencia 2.
*[EN]: - Mitigation when the LLM misclassifies an urgency 5 as urgency 2.*

## Guardrails implementados
*[EN]: Implemented guardrails*

- **Prompt injection**: patrones detectados en `agent.detect_prompt_injection`.
*[EN]: - Prompt injection: patterns detected in `agent.detect_prompt_injection`.*
- **PII redaction**: `agent.redact_pii` redacta email/teléfono/tarjeta antes de mandar al LLM.
*[EN]: - PII redaction: `agent.redact_pii` redacts email/phone/card before sending to the LLM.*
- **Escalado humano**: tool `flag_for_human_review` persiste en `outputs/human_review_queue.jsonl`.
*[EN]: - Human escalation: `flag_for_human_review` tool persists to `outputs/human_review_queue.jsonl`.*

## Limitaciones conocidas
*[EN]: Known limitations*

(Documenta aquí lo que no llegaste a cubrir y por qué.)
*[EN]: (Document here what you did not manage to cover and why.)*

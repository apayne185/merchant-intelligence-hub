# Parte 4 · API de clasificación de reclamaciones — `src/parte4_api/`

> Este README es **tu** entregable: cuando termines, descríbelo desde la perspectiva del evaluador. Reemplaza las secciones que necesites con tu implementación real.

## Arquitectura

```
parte4_api/
├── main.py        ← FastAPI app + endpoints + dependency injection
├── agent.py       ← agente Agno + tools + guardrails + PII redaction
├── schemas.py     ← Pydantic v2 models (request/response)
└── README.md      ← este archivo
```

## Cómo arrancar (el evaluador ejecutará exactamente esto)

```bash
# Opción A · con clave OpenAI propia
export OPENAI_API_KEY=sk-...
uvicorn src.parte4_api.main:app --reload --port 8000

# Opción B · sin clave (LLM mockeado, determinístico)
export MOCK_LLM=1
uvicorn src.parte4_api.main:app --reload --port 8000
```

## Endpoints

### `GET /health`

```bash
curl -s http://localhost:8000/health | jq
```

Respuesta:
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

## Decisiones técnicas

Documenta en `DECISIONS.md`:

- Por qué Agno (vs LangChain/LlamaIndex).
- Modelo elegido + estimación de coste mensual procesando 5.000 emails/día.
- Trade-offs del schema Pydantic (¿por qué enum cerrado? ¿por qué cap 300 chars en `reasoning`?).
- Cómo evaluarías la calidad antes de producción (golden set, LLM-as-judge, métricas).
- Mitigación cuando el LLM clasifica mal una urgencia 5 como urgencia 2.

## Guardrails implementados

- **Prompt injection**: patrones detectados en `agent.detect_prompt_injection`.
- **PII redaction**: `agent.redact_pii` redacta email/teléfono/tarjeta antes de mandar al LLM.
- **Escalado humano**: tool `flag_for_human_review` persiste en `outputs/human_review_queue.jsonl`.

## Limitaciones conocidas

(Documenta aquí lo que no llegaste a cubrir y por qué.)

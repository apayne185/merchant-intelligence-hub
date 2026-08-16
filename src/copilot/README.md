# Merchant Intelligence Copilot — `src/copilot/`
*[EN]: Merchant Intelligence Copilot — `src/copilot/`*

Multi-agent orchestrator that answers natural-language merchant questions by
routing to specialist tools — real SQL/KPI queries, the churn-risk model,
policy-doc RAG, and the complaint classifier (`src/parte4_api/`) — and
returns a structured, cited answer. Built on top of the pipeline in
`src/parte1_pandas.py`/`parte2_sql.sql`/`parte3_modeling.ipynb`, not a
rewrite of it.
*[EN]: Multi-agent orchestrator answering natural-language merchant
questions by routing to specialist tools — real SQL/KPI queries, the
churn-risk model, policy-doc RAG, and the complaint classifier
(`src/parte4_api/`) — returning a structured, cited answer. Built on top of
the pipeline in `src/parte1_pandas.py`/`parte2_sql.sql`/`parte3_modeling.ipynb`,
not a rewrite of it.*

## Arquitectura
*[EN]: Architecture*

```
copilot/
├── api.py                    ← FastAPI app: POST /ask, GET /health
├── graph.py                  ← LangGraph wiring: tool nodes + build_graph() + pick_next()
├── router.py                 ← route_mock() (keywords) / route_real() (Agno, structured output)
├── synthesis.py               ← synthesize_mock() (templated) / synthesize_real() (Agno, grounded)
├── state.py                    ← CopilotState (LangGraph state) + initial_state()
├── schemas.py                   ← Citation, ToolCallRecord, RouteDecision, AskRequest/Response
├── retrieval_core.py             ← shared vector store/embedder, also used by parte4_api/retrieval.py
├── README.md                      ← este archivo / this file
└── tools/
    ├── data_analyst.py       ← DuckDB KPI queries over src/parte1_pandas.py's load_clean()
    ├── risk.py                ← churn model scoring + per-instance SHAP, over outputs/model.pkl
    ├── grounding.py             ← policy-doc RAG over data/policy_docs.json
    └── complaint_classifier.py   ← thin wrapper over src/parte4_api/agent.py's build_agent()
```

```
START → route → {data_analyst | risk | grounding | complaint_classifier}* → synthesize → END
```

`route` decides which specialist(s) a question needs, once. Each specialist
node runs in turn, appending its own facts/citations to the state; a pure
Python function (`pick_next`, no LLM call) pops the next one off the queue.
`synthesize` writes the final answer from everything gathered. See
`DECISIONS.md` D26 for why this is a bounded worker-queue instead of a
parallel fan-out.

## Cómo arrancar
*[EN]: How to start*

```bash
# Opción A · sin clave OpenAI (determinístico, cero coste)
# [EN]: Option A · without an OpenAI key (deterministic, zero cost)
export MOCK_LLM=1
uvicorn src.copilot.api:app --reload --port 8001

# Opción B · con clave OpenAI propia
# [EN]: Option B · with your own OpenAI key
export OPENAI_API_KEY=sk-...
uvicorn src.copilot.api:app --reload --port 8001
```

Corre de forma independiente de `src/parte4_api/main.py` (puerto 8000) — el
copilot reutiliza el agente de clasificación **en proceso**, no por HTTP, así
que no hay necesidad de correr ambos servicios para probar `/ask`. Ver
`DECISIONS.md` D27.
*[EN]: Runs independently of `src/parte4_api/main.py` (port 8000) — the
copilot reuses the classifier agent **in-process**, not over HTTP, so
there's no need to run both services to try `/ask`. See `DECISIONS.md` D27.*

**Nota de arranque en frío**: la primera pregunta que toca `data_analyst` o
`risk` en un proceso nuevo carga y limpia el CSV de transacciones
(`load_clean()`) — instantáneo con el fixture pequeño, pero varios segundos
con el CSV real de ~200k filas si lo tienes en `data/transactions_sample.csv`.
Preguntas siguientes son rápidas (`get_clean_transactions()` cachea por ruta).
*[EN]: **Cold-start note**: the first question that touches `data_analyst` or
`risk` in a fresh process loads and cleans the transactions CSV
(`load_clean()`) — instant with the small fixture, but several seconds with
the real ~200k-row CSV if you have it at `data/transactions_sample.csv`.
Subsequent questions are fast (`get_clean_transactions()` caches by path).*

## Endpoints

### `GET /health`

```bash
curl -s http://localhost:8001/health | jq
```

### `POST /ask`

```bash
curl -s -X POST http://localhost:8001/ask \
  -H 'Content-Type: application/json' \
  -d '{
    "question": "Which merchants are trending toward churn and why, and does anything in our onboarding policy flag them?"
  }' | jq
```

Respuesta (recortada):
*[EN]: Response (trimmed):*
```json
{
  "question": "Which merchants are trending toward churn and why...",
  "route": ["risk", "grounding"],
  "answer": "Merchant 10004947 churn risk: medium (16% probability)... This model's discrimination is weak (ROC-AUC 0.58...)... Per policy RP-04 (Churn-risk escalation policy)...",
  "citations": [
    {"source_type": "model_output", "id": "churn_model:10004947", "title": "Churn score for merchant 10004947", "excerpt": "..."},
    {"source_type": "policy_doc", "id": "RP-04", "title": "Churn-risk escalation policy", "excerpt": "..."}
  ],
  "tool_calls": [
    {"tool": "risk", "args": {"merchant_ids": [10004947, 10009641, 10008563], "via_heuristic_shortlist": true}, "summary": "scored 3 merchant(s) against the churn model"},
    {"tool": "grounding", "args": {"k": 3}, "summary": "retrieved 3 policy doc(s)"}
  ],
  "mode": "mock",
  "latency_ms": 65
}
```

`merchant_id` opcional en el request — si se omite y la pregunta necesita
`risk`, el Risk tool usa la heurística de `merchants_at_risk` (`DECISIONS.md`
D3) para elegir candidatos a puntuar en vez de puntuar todos los merchants.
*[EN]: `merchant_id` is optional in the request — if omitted and the
question needs `risk`, the Risk tool uses the `merchants_at_risk` heuristic
(`DECISIONS.md` D3) to pick candidates to score instead of scoring every
merchant.*

## Tests

```bash
MOCK_LLM=1 pytest -v tests/test_copilot_*.py tests/conftest.py
```

Todo corre en modo mock por defecto (sin `OPENAI_API_KEY`). Los tests de
`data_analyst`/`risk`/`graph`/`api` fuerzan el fixture pequeño
(`tests/conftest.py:force_fixture_csv`) incluso si el CSV real está presente
localmente, para que el comportamiento sea determinístico y coincida con lo
que ve CI.
*[EN]: Everything runs in mock mode by default (no `OPENAI_API_KEY`).
`data_analyst`/`risk`/`graph`/`api` tests force the small fixture
(`tests/conftest.py:force_fixture_csv`) even if the real CSV happens to be
present locally, so behavior is deterministic and matches what CI sees.*

## Eval

```bash
MOCK_LLM=1 uv run python -m scripts.evaluate_copilot
# outputs/eval_report_copilot.json — route accuracy, citation hallucination
# rate, risk-caveat mention rate, classification accuracy. See DECISIONS.md D28.
```

## Decisiones técnicas
*[EN]: Technical decisions*

Ver `DECISIONS.md`, sección "Parte 6 · Merchant Intelligence Copilot"
(D22-D29): extracción del vector store compartido, por qué SQL parametrizado
y no generado por LLM, el hallazgo honesto sobre el score del modelo de
churn en inferencia en vivo, por qué una cola acotada en vez de fan-out
paralelo de LangGraph, por qué Agno sigue siendo el framework de LLM dentro
de cada nodo, y los dos bugs reales que encontró construir el golden set.
*[EN]: See `DECISIONS.md`, "Parte 6 · Merchant Intelligence Copilot" section
(D22-D29): the shared vector store extraction, why parameterized SQL instead
of LLM-generated SQL, the honest finding about the churn model's score in
live inference, why a bounded queue instead of LangGraph's parallel fan-out,
why Agno remains the LLM framework inside every node, and the two real bugs
building the golden set found.*

## Limitaciones conocidas
*[EN]: Known limitations*

- **El router en modo mock es por palabras clave, no semántico**: `route_mock`
  (`router.py`) usa regex simples — una pregunta parafraseada de forma muy
  distinta a los ejemplos del golden set podría no enrutar como se espera.
  Solo el modo real (Agno + `output_schema=RouteDecision`) hace clasificación
  semántica de verdad.
*[EN]: - Mock-mode routing is keyword-based, not semantic: `route_mock`
  (`router.py`) uses simple regexes — a question phrased very differently
  from the golden set's examples might not route as expected. Only real
  mode (Agno + `output_schema=RouteDecision`) does genuine semantic
  classification.*
- **Sin memoria entre requests**: `/ask` es Q&A de un solo turno — no hay
  checkpointer de LangGraph, cada request es independiente. Ver `DECISIONS.md`
  D26.
*[EN]: - No memory across requests: `/ask` is single-turn Q&A — no LangGraph
  checkpointer, every request is independent. See `DECISIONS.md` D26.*
- **`score_merchant` recalcula features sobre todo el DataFrame en cada
  llamada**: aceptable a escala del fixture o del CSV real (~10k merchants),
  no optimizado para puntuar en batch. Ver `DECISIONS.md` D24.
*[EN]: - `score_merchant` recomputes features over the whole DataFrame on
  every call: acceptable at fixture or real-CSV (~10k merchant) scale, not
  optimized for batch scoring. See `DECISIONS.md` D24.*
- **Ruta real (no-mock) no probada end-to-end**: igual que `parte4_api` (ver
  su README, "Limitaciones conocidas") — sin `OPENAI_API_KEY` disponible en
  este entorno, `route_real`/`synthesize_real` están verificados por
  construcción (tests con `Agent.run` mockeado), no contra una respuesta real
  de OpenAI.
*[EN]: - Real (non-mock) path not tested end-to-end: same as `parte4_api`
  (see its README, "Known limitations") — without an `OPENAI_API_KEY`
  available in this environment, `route_real`/`synthesize_real` are verified
  for construction (tests with a mocked `Agent.run`), not against a real
  OpenAI response.*
- **Sin autenticación ni rate limiting**: mismo alcance que `parte4_api` — ver
  su README.
*[EN]: - No auth or rate limiting: same scope as `parte4_api` — see its
  README.*

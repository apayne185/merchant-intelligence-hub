# Enunciado oficial · Take-Home Getnet AI Lab Graduate Program 2026
*[EN]: Official brief · Take-Home Getnet AI Lab Graduate Program 2026*

**Versión 1.0 · Mayo 2026**
*[EN]: Version 1.0 · May 2026*

Lee este documento entero antes de empezar. El `README.md` cubre setup y entrega; este archivo cubre **qué tienes que construir**.
*[EN]: Read this entire document before starting. `README.md` covers setup and delivery; this file covers what you have to build.*

---

## Contexto
*[EN]: Context*

Eres candidato/a al Graduate Program del **AI Lab Getnet**. Este test mide cómo piensas y comunicas tus decisiones técnicas, no cuánto código produces.
*[EN]: You are a candidate for the Getnet AI Lab Graduate Program. This test measures how you think and communicate your technical decisions, not how much code you produce.*

**Asumimos que vas a usar LLMs.** No los prohibimos. Lo importante es: ¿entiendes lo que has escrito? ¿Detectas lo que falla? ¿Eres honesto sobre lo que no sabes?
*[EN]: We assume you will use LLMs. We do not prohibit them. What matters is: do you understand what you have written? Do you detect what fails? Are you honest about what you do not know?*

---

## Dataset proporcionado
*[EN]: Provided dataset*

Recibes el archivo `data/transactions_sample.csv` (~200k filas, ~80 MB) con transacciones simuladas de merchants en Brasil:
*[EN]: You receive the file `data/transactions_sample.csv` (~200k rows, ~80 MB) with simulated merchant transactions in Brazil:*

```
transaction_id (int)
merchant_id (int)
transaction_date (string)         
amount (string)                   
status (str)                      # 'approved' | 'denied' | 'reversed'
channel (str)                     # 'pos' | 'ecom' | 'pix' | 'tef'
cancellation_reason (str | NA)    
reference_date (date)             # snapshot del análisis / analysis snapshot
fla_churn90 (int)                 # target: 1 si el merchant churneó en 90 días / 1 if the merchant churned in 90 days
last_complaint_date (date | NA)
segment (str)                     # 'SMB' | 'MidMarket' | 'Enterprise'
mcc (str)
```

Además, `data/merchants_context.json` contiene ~500 merchants con `segment`, `tpv_last_3m`, `n_complaints_30d`, `days_since_last_complaint`. Lo usarás en Parte 4.
*[EN]: Additionally, `data/merchants_context.json` contains ~500 merchants with `segment`, `tpv_last_3m`, `n_complaints_30d`, `days_since_last_complaint`. You will use it in Part 4.*

---

## Parte 1 · Análisis exploratorio en pandas (15 pts)
*[EN]: Part 1 · Exploratory analysis in pandas (15 pts)*

Implementa `src/parte1_pandas.py`. Entrega 4 funciones:
*[EN]: Implement `src/parte1_pandas.py`. Deliver 4 functions:*

1. **`load_clean(path: str) -> pd.DataFrame`** — carga el CSV y devuelve un DataFrame **listo para análisis**. Documenta en docstring qué decisiones tomaste.
   *[EN]: — loads the CSV and returns a DataFrame ready for analysis. Document in the docstring what decisions you made.*
2. **`monthly_kpis(df) -> pd.DataFrame`** — KPIs mensuales por merchant: `tpv`, `approval_rate`, `pct_ecom`, `n_tx`. Vectorizado, sin loops.
   *[EN]: — Monthly KPIs per merchant: `tpv`, `approval_rate`, `pct_ecom`, `n_tx`. Vectorized, no loops.*
3. **`quality_report(df) -> dict`** — reporta al menos 5 problemas de calidad de datos que detectes. Cada problema con (a) qué columna, (b) cuántas filas afectadas, (c) qué impacto tiene, (d) cómo lo resolverías.
   *[EN]: — reports at least 5 data quality issues you detect. Each issue with (a) which column, (b) how many rows affected, (c) what impact it has, (d) how you would fix it.*
4. **`merchants_at_risk(df, top_n: int = 200) -> pd.DataFrame`** — devuelve los N merchants con mayor "señal débil" de pre-churn. Tú decides la heurística: justifícala en `DECISIONS.md`.
   *[EN]: — returns the N merchants with the highest pre-churn "weak signal". You decide the heuristic: justify it in `DECISIONS.md`.*

---

## Parte 2 · SQL sobre warehouse (10 pts)
*[EN]: Part 2 · SQL on warehouse (10 pts)*

Escribe las queries en `src/parte2_sql.sql` (no necesitas ejecutarlas, escríbelas en estilo Spark SQL / Databricks SQL y comenta supuestos). Esquema:
*[EN]: Write the queries in `src/parte2_sql.sql` (you do not need to run them, write them in Spark SQL / Databricks SQL style and comment on assumptions). Schema:*

```sql
merchants(merchant_id, country, mcc, onboarding_date, segment)
transactions(transaction_id, merchant_id, transaction_date, amount, status, channel, dat_process)
churn_labels(merchant_id, reference_date, fla_churn90)
```

**Q1 (3 pts).** Top 10 merchants brasileños por TPV aprobado de Q3 2025. Devuelve `merchant_id`, `tpv`, `approval_rate`, `mcc`.
*[EN]: Q1 (3 pts). Top 10 Brazilian merchants by approved TPV for Q3 2025. Return `merchant_id`, `tpv`, `approval_rate`, `mcc`.*

**Q2 (3 pts).** Para cada `country × segment`, % de merchants con `fla_churn90 = 1` a `reference_date = '2025-09-30'`. Solo segmentos con ≥ 100 merchants.
*[EN]: Q2 (3 pts). For each `country × segment`, % of merchants with `fla_churn90 = 1` at `reference_date = '2025-09-30'`. Only segments with ≥ 100 merchants.*

**Q3 (3 pts).** Para cada merchant, TPV mensual y TPV mismo mes año anterior (YoY) — 2025 vs 2024.
*[EN]: Q3 (3 pts). For each merchant, monthly TPV and TPV same month prior year (YoY) — 2025 vs 2024.*

**Q4 (1 pt).** En 2 líneas: ¿qué ventaja te da que `transactions` esté **particionada por `dat_process`** al hacer la Q1?
*[EN]: Q4 (1 pt). In 2 lines: what advantage does it give you that `transactions` is partitioned by `dat_process` when doing Q1?*

---

## Parte 3 · Modelado ML (15 pts)
*[EN]: Part 3 · ML Modelling (15 pts)*

Implementa `src/parte3_modeling.ipynb`. Entrena un modelo que prediga `fla_churn90` con el dataset proporcionado.
*[EN]: Implement `src/parte3_modeling.ipynb`. Train a model that predicts `fla_churn90` with the provided dataset.*

Requisitos:
*[EN]: Requirements:*
- **Pipeline** con al menos un modelo. / *with at least one model.*
- **Métricas** correctas para tu modelo. / *Correct **metrics** for your model.*
- **Interpretabilidad**: top-5 features importantes con justificación. / ***Interpretability**: top-5 important features with justification.*
- **Documenta en `DECISIONS.md`**: ¿qué features descartaste y por qué? ¿hay alguna que sospeches que sea *trampa*? / ***Document in `DECISIONS.md`**: what features did you discard and why? Is there any you suspect is a *trap*?*

---

## Parte 4 · FastAPI + Agno agent — implementación funcional (15 pts)
*[EN]: Part 4 · FastAPI + Agno agent — functional implementation (15 pts)*

No basta con diseñarlo: tienes que **construirlo, arrancarlo con `uvicorn` y que devuelva JSON real**. Esta parte es la que mejor mide si entiendes la stack del lab.
*[EN]: It is not enough to design it: you have to build it, start it with `uvicorn` and have it return real JSON. This part best measures whether you understand the lab's stack.*

**Stack obligatorio (no negociable):**
*[EN]: Required stack (non-negotiable):*

- **FastAPI** + **`uvicorn`** como servidor ASGI. / *as the ASGI server.*
- **Pydantic v2** para validación de request/response (schemas tipados, no `dict`). / *for request/response validation (typed schemas, not `dict`).*
- **Agno** como framework del agente (ya en `pyproject.toml`, instalado con `uv sync --extra dev`). Docs: <https://docs.agno.com>. Si quieres otro framework, puedes usarlo pero tienes que documentarlo y modificar el codigo. / *as the agent framework (already in `pyproject.toml`, installed with `uv sync --extra dev`). If you want another framework, you can use it but you must document it and modify the code.*
- **OpenAI** como model provider o **`MOCK_LLM=1`** (ver §LLM provider en `README.md`). / *as the model provider or **`MOCK_LLM=1`** (see §LLM provider in `README.md`).*

### Especificación funcional
*[EN]: Functional specification*

3 endpoints obligatorios:
*[EN]: 3 required endpoints:*

**1. `GET /health`** → `{"status": "ok", "model": "<nombre / name>", "version": "<git short sha o / or '0.1.0'>"}`

**2. `POST /classify`** — clasifica una reclamación. / *classifies a complaint.*

Request:
```json
{
  "merchant_id": 10063716,
  "email_text": "Hola, llevo 3 días sin poder cobrar con mi POS, ya he llamado dos veces y nadie me responde. Voy a cancelar la cuenta.",
  "locale": "es"
}
```

Response 200 (schema **estricto**, valida con Pydantic / **strict** schema, validated with Pydantic):
```json
{
  "merchant_id": 10063716,
  "category": "technical_issue",
  "urgency": 5,
  "requires_human_escalation": true,
  "reasoning": "...",
  "merchant_context_used": true,
  "latency_ms": 1234
}
```

Categorías (enum cerrado / closed enum): `technical_issue` · `billing` · `onboarding` · `fraud` · `churn_threat` · `other`.
Urgency: int 1..5. Reasoning ≤ 300 chars.

**3. `POST /classify/batch`** — lista de hasta 50 reclamaciones, procesamiento concurrente (`asyncio.gather`). Devuelve lista + `total_latency_ms` + `n_failed`.
*[EN]: — list of up to 50 complaints, concurrent processing (`asyncio.gather`). Returns list + `total_latency_ms` + `n_failed`.*

### Requisitos del agente Agno (obligatorios)
*[EN]: Agno agent requirements (required)*

En `src/parte4_api/agent.py`:
*[EN]: In `src/parte4_api/agent.py`:*

1. **`Agent` de Agno** con `instructions` claras y `response_model` Pydantic (structured output forzado). / *Agno **`Agent`** with clear `instructions` and a Pydantic `response_model` (forced structured output).*
2. **≥ 2 tools custom**:
   - `get_merchant_context(merchant_id: int) -> dict` → lee `data/merchants_context.json` y devuelve `segment`, `tpv_last_3m`, `n_complaints_30d`, `days_since_last_complaint`. / *reads `data/merchants_context.json` and returns `segment`, `tpv_last_3m`, `n_complaints_30d`, `days_since_last_complaint`.*
   - `flag_for_human_review(merchant_id: int, reason: str) -> dict` → registra en `outputs/human_review_queue.jsonl`. Side-effect real. / *records in `outputs/human_review_queue.jsonl`. Real side-effect.*
3. **Guardrail prompt injection**: si `email_text` contiene patrones tipo `ignore previous instructions`, `system:`, etc. → devuelve `category="other"`, `urgency=1`, `requires_human_escalation=true`, `reasoning="prompt_injection_detected"`. / *if `email_text` contains patterns like `ignore previous instructions`, `system:`, etc. → returns `category="other"`, `urgency=1`, `requires_human_escalation=true`, `reasoning="prompt_injection_detected"`.*
4. **PII redaction**: redactar emails, teléfonos y números de tarjeta con regex antes de mandar al LLM. / *redact emails, phone numbers and card numbers with regex before sending to the LLM.*

### Cómo arrancarlo
*[EN]: How to start it*

En `src/parte4_api/README.md` documenta este comando exacto (el evaluador lo ejecutará):
*[EN]: In `src/parte4_api/README.md` document this exact command (the evaluator will run it):*

```bash
export OPENAI_API_KEY=sk-...   # o export MOCK_LLM=1 / or export MOCK_LLM=1
uvicorn src.parte4_api.main:app --reload --port 8000
```

Si **no arranca con ese comando**, pierdes la mitad de los puntos de Parte 4 aunque el código sea correcto.
*[EN]: If it does not start with that command, you lose half the Part 4 points even if the code is correct.*

### Tests obligatorios (`tests/test_api.py`)
*[EN]: Required tests (`tests/test_api.py`)*

Con `from fastapi.testclient import TestClient`. Cobertura mínima:
*[EN]: With `from fastapi.testclient import TestClient`. Minimum coverage:*

- `test_health()` — 200 + schema correcto. / *200 + correct schema.*
- `test_classify_happy_path()` — email normal → categoría válida, urgency en rango. / *normal email → valid category, urgency in range.*
- `test_classify_prompt_injection()` — email con `"ignore previous instructions"` → `reasoning="prompt_injection_detected"`.
- `test_classify_invalid_input()` — falta `email_text` → 422. / *missing `email_text` → 422.*
- `test_batch_concurrency()` — 10 emails, todos con respuesta válida. / *10 emails, all with valid response.*

Los tests deben pasar con **el LLM mockeado**. Truco: inyecta el cliente LLM con `Depends(get_llm)` para poder sustituirlo en tests.
*[EN]: Tests must pass with the mocked LLM. Tip: inject the LLM client with `Depends(get_llm)` to be able to replace it in tests.*

### Documentar en `DECISIONS.md` para Parte 4
*[EN]: Document in `DECISIONS.md` for Part 4*

- Modelo elegido + estimación de coste mensual procesando 5.000 emails/día. / *Chosen model + monthly cost estimate processing 5,000 emails/day.*
- Trade-offs de tu schema Pydantic. / *Trade-offs of your Pydantic schema.*
- Cómo evaluarías la calidad antes de producción. / *How you would evaluate quality before production.*
- Qué pasa cuando el LLM se equivoca clasificando una urgencia 5 como 2 — ¿cómo lo mitigas? / *What happens when the LLM mistakenly classifies an urgency 5 as 2 — how do you mitigate it?*

---

## Parte 5 · Pregunta
*[EN]: Part 5 · Question*

Implementa en `src/parte5_bonus.py`:
*[EN]: Implement in `src/parte5_bonus.py`:*

```python
def detect_collusion_rings(transactions: pd.DataFrame) -> list[set[int]]:
    """
    Detecta grupos de >= 3 merchants que muestran señales de colusión
    (transacciones cruzadas anómalas en patrones grafos).
    Devuelve lista de sets con merchant_ids del posible ring.
    [EN]: Detects groups of >= 3 merchants showing collusion signals
    [EN]: (anomalous cross-transactions in graph patterns).
    [EN]: Returns a list of sets with merchant_ids of the possible ring.
    """
```

> **No esperamos que la resuelvas en este test.** Si la haces con un grafo NetworkX bien hecho, tienes bonus. Lo importante: si **no puedes**, escribe en `DECISIONS.md` (a) por qué este problema es difícil, (b) qué datos pedirías, (c) qué algoritmos investigarías, (d) qué tiempo realista necesitarías. **Eso vale más que un intento pobre.**
> *[EN]: We do not expect you to solve it in this test. If you do it with a well-built NetworkX graph, you get a bonus. What matters: if you cannot, write in `DECISIONS.md` (a) why this problem is difficult, (b) what data you would request, (c) what algorithms you would investigate, (d) what realistic time you would need. That is worth more than a poor attempt.*

---

## Documentos de criterio obligatorios
*[EN]: Required criterion documents*

Copia las plantillas de `templates/` a la raíz y rellénalas:
*[EN]: Copy the templates from `templates/` to the root and fill them in:*

- **`DECISIONS.md`** — por cada decisión: qué hice / por qué / qué descarté / qué supuse. Mínimo 6 decisiones cubriendo Partes 1, 3 y 4. / *for each decision: what I did / why / what I discarded / what I assumed. Minimum 6 decisions covering Parts 1, 3 and 4.*
- **`ASSUMPTIONS.md`** — el enunciado tiene **3 ambigüedades intencionales**. Listalas y di qué supusiste para cada una y cómo lo verificarías con un stakeholder real. / *the brief has **3 intentional ambiguities**. List them and say what you assumed for each and how you would verify it with a real stakeholder.*
- **`SELF_REVIEW.md`** — identifica **≥ 3 problemas de tu propia solución**. Honestidad gana puntos. / *identify **≥ 3 problems with your own solution**. Honesty earns points.*
- **`TOOLS_USED.md`** — declara qué LLMs/IDEs usaste y para qué. No penaliza. / *declare what LLMs/IDEs you used and for what. Does not penalize.*


**Mucha suerte.** Cuando termines, vuelve al §8 del `README.md` para empaquetar y entregar.
*[EN]: Good luck. When you are done, return to §8 of `README.md` to package and deliver.*

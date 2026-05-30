# Enunciado oficial · Take-Home Getnet AI Lab Graduate Program 2026

**Versión 1.0 · Mayo 2026**

Lee este documento entero antes de empezar. El `README.md` cubre setup y entrega; este archivo cubre **qué tienes que construir**.

---

## Contexto

Eres candidato/a al Graduate Program del **AI Lab Getnet**. Este test mide cómo piensas y comunicas tus decisiones técnicas, no cuánto código produces.

**Asumimos que vas a usar LLMs.** No los prohibimos. Lo importante es: ¿entiendes lo que has escrito? ¿Detectas lo que falla? ¿Eres honesto sobre lo que no sabes?

---

## Dataset proporcionado

Recibes el archivo `data/transactions_sample.csv` (~200k filas, ~80 MB) con transacciones simuladas de merchants en Brasil:

```
transaction_id (int)
merchant_id (int)
transaction_date (string)         
amount (string)                   
status (str)                      # 'approved' | 'denied' | 'reversed'
channel (str)                     # 'pos' | 'ecom' | 'pix' | 'tef'
cancellation_reason (str | NA)    
reference_date (date)             # snapshot del análisis
fla_churn90 (int)                 # target: 1 si el merchant churneó en 90 días
last_complaint_date (date | NA)
segment (str)                     # 'SMB' | 'MidMarket' | 'Enterprise'
mcc (str)
```

Además, `data/merchants_context.json` contiene ~500 merchants con `segment`, `tpv_last_3m`, `n_complaints_30d`, `days_since_last_complaint`. Lo usarás en Parte 4.

---

## Parte 1 · Análisis exploratorio en pandas (15 pts)

Implementa `src/parte1_pandas.py`. Entrega 4 funciones:

1. **`load_clean(path: str) -> pd.DataFrame`** — carga el CSV y devuelve un DataFrame **listo para análisis**. Documenta en docstring qué decisiones tomaste.
2. **`monthly_kpis(df) -> pd.DataFrame`** — KPIs mensuales por merchant: `tpv`, `approval_rate`, `pct_ecom`, `n_tx`. Vectorizado, sin loops.
3. **`quality_report(df) -> dict`** — reporta al menos 5 problemas de calidad de datos que detectes. Cada problema con (a) qué columna, (b) cuántas filas afectadas, (c) qué impacto tiene, (d) cómo lo resolverías.
4. **`merchants_at_risk(df, top_n: int = 200) -> pd.DataFrame`** — devuelve los N merchants con mayor "señal débil" de pre-churn. Tú decides la heurística: justifícala en `DECISIONS.md`.

---

## Parte 2 · SQL sobre warehouse (10 pts)

Escribe las queries en `src/parte2_sql.sql` (no necesitas ejecutarlas, escríbelas en estilo Spark SQL / Databricks SQL y comenta supuestos). Esquema:

```sql
merchants(merchant_id, country, mcc, onboarding_date, segment)
transactions(transaction_id, merchant_id, transaction_date, amount, status, channel, dat_process)
churn_labels(merchant_id, reference_date, fla_churn90)
```

**Q1 (3 pts).** Top 10 merchants brasileños por TPV aprobado de Q3 2025. Devuelve `merchant_id`, `tpv`, `approval_rate`, `mcc`.

**Q2 (3 pts).** Para cada `country × segment`, % de merchants con `fla_churn90 = 1` a `reference_date = '2025-09-30'`. Solo segmentos con ≥ 100 merchants.

**Q3 (3 pts).** Para cada merchant, TPV mensual y TPV mismo mes año anterior (YoY) — 2025 vs 2024.

**Q4 (1 pt).** En 2 líneas: ¿qué ventaja te da que `transactions` esté **particionada por `dat_process`** al hacer la Q1?

---

## Parte 3 · Modelado ML (15 pts)

Implementa `src/parte3_modeling.ipynb`. Entrena un modelo que prediga `fla_churn90` con el dataset proporcionado.

Requisitos:
- **Pipeline** con al menos un modelo.
- **Métricas** correctas para tu modelo.
- **Interpretabilidad**: top-5 features importantes con justificación. 
- **Documenta en `DECISIONS.md`**: ¿qué features descartaste y por qué? ¿hay alguna que sospeches que sea *trampa*?

---

## Parte 4 · FastAPI + Agno agent — implementación funcional (15 pts)

No basta con diseñarlo: tienes que **construirlo, arrancarlo con `uvicorn` y que devuelva JSON real**. Esta parte es la que mejor mide si entiendes la stack del lab.

**Stack obligatorio (no negociable):**

- **FastAPI** + **`uvicorn`** como servidor ASGI.
- **Pydantic v2** para validación de request/response (schemas tipados, no `dict`).
- **Agno** como framework del agente (ya en `pyproject.toml`, instalado con `uv sync --extra dev`). Docs: <https://docs.agno.com>. Si quieres otro framework, puedes usarlo pero tienes que documentarlo y modificar el codigo.
- **OpenAI** como model provider o **`MOCK_LLM=1`** (ver §LLM provider en `README.md`).

### Especificación funcional

3 endpoints obligatorios:

**1. `GET /health`** → `{"status": "ok", "model": "<nombre>", "version": "<git short sha o '0.1.0'>"}`

**2. `POST /classify`** — clasifica una reclamación.

Request:
```json
{
  "merchant_id": 10063716,
  "email_text": "Hola, llevo 3 días sin poder cobrar con mi POS, ya he llamado dos veces y nadie me responde. Voy a cancelar la cuenta.",
  "locale": "es"
}
```

Response 200 (schema **estricto**, valida con Pydantic):
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

Categorías (enum cerrado): `technical_issue` · `billing` · `onboarding` · `fraud` · `churn_threat` · `other`.
Urgency: int 1..5. Reasoning ≤ 300 chars.

**3. `POST /classify/batch`** — lista de hasta 50 reclamaciones, procesamiento concurrente (`asyncio.gather`). Devuelve lista + `total_latency_ms` + `n_failed`.

### Requisitos del agente Agno (obligatorios)

En `src/parte4_api/agent.py`:

1. **`Agent` de Agno** con `instructions` claras y `response_model` Pydantic (structured output forzado).
2. **≥ 2 tools custom**:
   - `get_merchant_context(merchant_id: int) -> dict` → lee `data/merchants_context.json` y devuelve `segment`, `tpv_last_3m`, `n_complaints_30d`, `days_since_last_complaint`.
   - `flag_for_human_review(merchant_id: int, reason: str) -> dict` → registra en `outputs/human_review_queue.jsonl`. Side-effect real.
3. **Guardrail prompt injection**: si `email_text` contiene patrones tipo `ignore previous instructions`, `system:`, etc. → devuelve `category="other"`, `urgency=1`, `requires_human_escalation=true`, `reasoning="prompt_injection_detected"`.
4. **PII redaction**: redactar emails, teléfonos y números de tarjeta con regex antes de mandar al LLM.

### Cómo arrancarlo

En `src/parte4_api/README.md` documenta este comando exacto (el evaluador lo ejecutará):

```bash
export OPENAI_API_KEY=sk-...   # o export MOCK_LLM=1
uvicorn src.parte4_api.main:app --reload --port 8000
```

Si **no arranca con ese comando**, pierdes la mitad de los puntos de Parte 4 aunque el código sea correcto.

### Tests obligatorios (`tests/test_api.py`)

Con `from fastapi.testclient import TestClient`. Cobertura mínima:

- `test_health()` — 200 + schema correcto.
- `test_classify_happy_path()` — email normal → categoría válida, urgency en rango.
- `test_classify_prompt_injection()` — email con `"ignore previous instructions"` → `reasoning="prompt_injection_detected"`.
- `test_classify_invalid_input()` — falta `email_text` → 422.
- `test_batch_concurrency()` — 10 emails, todos con respuesta válida.

Los tests deben pasar con **el LLM mockeado**. Truco: inyecta el cliente LLM con `Depends(get_llm)` para poder sustituirlo en tests.

### Documentar en `DECISIONS.md` para Parte 4

- Modelo elegido + estimación de coste mensual procesando 5.000 emails/día.
- Trade-offs de tu schema Pydantic.
- Cómo evaluarías la calidad antes de producción.
- Qué pasa cuando el LLM se equivoca clasificando una urgencia 5 como 2 — ¿cómo lo mitigas?

---

## Parte 5 · Pregunta

Implementa en `src/parte5_bonus.py`:

```python
def detect_collusion_rings(transactions: pd.DataFrame) -> list[set[int]]:
    """
    Detecta grupos de >= 3 merchants que muestran señales de colusión
    (transacciones cruzadas anómalas en patrones grafos).
    Devuelve lista de sets con merchant_ids del posible ring.
    """
```

> **No esperamos que la resuelvas en este test.** Si la haces con un grafo NetworkX bien hecho, tienes bonus. Lo importante: si **no puedes**, escribe en `DECISIONS.md` (a) por qué este problema es difícil, (b) qué datos pedirías, (c) qué algoritmos investigarías, (d) qué tiempo realista necesitarías. **Eso vale más que un intento pobre.**

---

## Documentos de criterio obligatorios

Copia las plantillas de `templates/` a la raíz y rellénalas:

- **`DECISIONS.md`** — por cada decisión: qué hice / por qué / qué descarté / qué supuse. Mínimo 6 decisiones cubriendo Partes 1, 3 y 4.
- **`ASSUMPTIONS.md`** — el enunciado tiene **3 ambigüedades intencionales**. Listalas y di qué supusiste para cada una y cómo lo verificarías con un stakeholder real.
- **`SELF_REVIEW.md`** — identifica **≥ 3 problemas de tu propia solución**. Honestidad gana puntos.
- **`TOOLS_USED.md`** — declara qué LLMs/IDEs usaste y para qué. No penaliza.


**Mucha suerte.** Cuando termines, vuelve al §8 del `README.md` para empaquetar y entregar.

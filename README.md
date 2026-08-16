# Merchant Intelligence Hub
#### Payne, Anna

[![CI](https://github.com/apayne185/merchant-intelligence-hub/actions/workflows/ci.yml/badge.svg)](https://github.com/apayne185/merchant-intelligence-hub/actions/workflows/ci.yml)

**Merchant Intelligence Copilot** — a multi-agent system that answers natural-language questions about merchants ("which merchants are trending toward churn and why, and does anything in our onboarding policy flag them?") by routing to specialist agents that call real tools — parameterized SQL/KPI queries, a churn-risk ML model, policy-doc RAG — and return a structured, cited answer, scored against a golden-set eval so reliability is measured, not just demoed.

Built on top of a data pipeline and risk-scoring project for merchant transaction data — ingestion, cleaning, KPI/SQL analysis, and a churn-risk ML model — that started as a technical assessment for the Getnet AI Lab Graduate Program and has been extended independently since, because the problem was worth continuing to build on. That original pipeline is exactly what the Copilot's tools call — see [Appendix: the underlying data and ML pipeline](#appendix-the-underlying-data-and-ml-pipeline-parte-1-5) below.


## Setup rápido
*Quick setup*

```bash
# Instalar dependencias (Python 3.10-3.13 , uv requerido)
uv sync --extra dev
# Sanity check del entorno
uv run python -c "import pandas, sklearn, fastapi, uvicorn, agno, langgraph, duckdb, pydantic; print('OK · environment ready')"
```

> **Nota Python 3.13**: `pandas` fue bumpeado de 2.2.2 a 2.2.3 (patch release, API idéntica) para compatibilidad con Python 3.13.

Atajos disponibles vía `Makefile` (`make help` para la lista completa):
*[EN]: Shortcuts available via `Makefile` (`make help` for the full list):*

```bash
make setup        # uv sync --extra dev
make test         # pytest -v con MOCK_LLM=1
make run-copilot  # uvicorn del Copilot (puerto 8001, MOCK_LLM=1 por defecto)
make eval-copilot # eval golden-set del Copilot
make precommit    # ruff + gitleaks (.pre-commit-config.yaml)
make lint         # ruff check
```


## Arrancar el Copilot
*Start the Copilot*

```bash
# Opción A - sin clave OpenAI (determinístico, cero coste)
export MOCK_LLM=1
uvicorn src.copilot.api:app --reload --port 8001

# Opción B - con clave OpenAI propia
export OPENAI_API_KEY=sk-...
uvicorn src.copilot.api:app --reload --port 8001
```

```bash
curl -s -X POST http://localhost:8001/ask \
  -H 'Content-Type: application/json' \
  -d '{"question": "Which merchants are trending toward churn and why, and does anything in our onboarding policy flag them?"}' \
  | python -m json.tool
```

Devuelve una respuesta estructurada y citada — `route` (qué agentes se
invocaron), `answer`, `citations` (con `source_type`: `policy_doc` /
`model_output` / `kpi_query` / `historical_case`), y `tool_calls` (auditoría
de qué se ejecutó). Ver [`src/copilot/README.md`](src/copilot/README.md)
para la arquitectura completa, ejemplos de respuesta, y limitaciones conocidas.
*[EN]: Returns a structured, cited answer — `route` (which agents were
invoked), `answer`, `citations` (with `source_type`: `policy_doc` /
`model_output` / `kpi_query` / `historical_case`), and `tool_calls` (audit
trail of what actually ran). See [`src/copilot/README.md`](src/copilot/README.md)
for the full architecture, response examples, and known limitations.*

### Arquitectura (resumen)
*Architecture (summary)*

```
START → route → {data_analyst | risk | grounding | complaint_classifier}* → synthesize → END
```

Un orquestador [LangGraph](https://github.com/langchain-ai/langgraph)
decide qué especialista(s) necesita una pregunta y en qué orden correrlos;
las llamadas a LLM dentro de cada nodo (routing en modo real, el clasificador
de reclamaciones, la síntesis final) siguen usando
[Agno](https://github.com/agno-agi/agno) — mismo framework de agentes que ya
estaba en el proyecto (Parte 4), no dos patrones distintos conviviendo. Todo
corre determinísticamente y a coste cero con `MOCK_LLM=1`, igual que el resto
del proyecto.
*[EN]: A [LangGraph](https://github.com/langchain-ai/langgraph) orchestrator
decides which specialist(s) a question needs and in what order to run them;
LLM calls inside each node (real-mode routing, the complaint classifier, the
final synthesis) still go through [Agno](https://github.com/agno-agi/agno) —
the same agent framework already in the project (Part 4), not two
competing patterns. Everything runs deterministically and at zero cost with
`MOCK_LLM=1`, same as the rest of the project.*

| Agente / Agent | Qué hace / What it does | Herramienta real detrás / Real tool behind it |
|---|---|---|
| Data Analyst | KPIs/SQL reales sobre transacciones / Real KPI/SQL over transactions | DuckDB, consultas parametrizadas — `src/copilot/tools/data_analyst.py` |
| Risk | Puntúa el riesgo de churn de un merchant / Scores a merchant's churn risk | `outputs/model.pkl` (LightGBM) + SHAP por instancia / per-instance SHAP — `tools/risk.py` |
| Grounding | RAG sobre políticas internas / RAG over internal policy | `data/policy_docs.json`, vector store propio / in-repo vector store — `tools/grounding.py` |
| Complaint classifier | Clasifica una reclamación pegada / Classifies a pasted complaint | Agente Agno existente (Parte 4) / existing Agno agent (Part 4) — `tools/complaint_classifier.py` |

Decisiones técnicas completas en `DECISIONS.md`, sección "Parte 6 · Merchant
Intelligence Copilot" (D22-D29) — incluye un hallazgo honesto sobre cómo el
modelo de churn puntúa en inferencia en vivo, no solo el mecanismo.
*[EN]: Full technical decisions in `DECISIONS.md`, "Parte 6 · Merchant
Intelligence Copilot" section (D22-D29) — includes an honest finding about
how the churn model scores in live inference, not just the mechanism.*


## Ejecutar tests
*Run tests*

```bash
MOCK_LLM=1 uv run pytest tests/ -v
#138 passed, 1 skipped (155 con --extra pyspark)
```


## Ejecutar eval del Copilot (golden set)
*Run the Copilot eval (golden set)*

```bash
MOCK_LLM=1 uv run python -m scripts.evaluate_copilot
#outputs/eval_report_copilot.json — route accuracy, tasa de alucinación de
#citas (0%), tasa de mención del caveat del modelo, accuracy de
#clasificación. Ver DECISIONS.md D28.
```

`scripts/check_eval_floors.py` corre en CI como gate real (no advisory) sobre
este reporte y el del clasificador de reclamaciones — ver "Protecciones del
repo" abajo.
*[EN]: `scripts/check_eval_floors.py` runs in CI as a real (not advisory)
gate over this report and the complaint classifier's — see "Repo
protections" below.*


## Protecciones del repo
*Repo protections*

- **Gate de CI real**: `.github/workflows/ci.yml` corre ambos harnesses de
  eval y falla el build si una métrica cae debajo de un piso anclado a un
  valor ya commiteado (`scripts/check_eval_floors.py`) — a diferencia del
  paso de Lint, que sigue siendo advisory por la deuda preexistente en los
  notebooks.
*[EN]: **Real CI gate**: `.github/workflows/ci.yml` runs both eval harnesses
  and fails the build if any metric drops below a floor anchored to an
  already-committed value (`scripts/check_eval_floors.py`) — unlike the
  Lint step, which stays advisory because of pre-existing notebook debt.*
- **Pre-commit**: `.pre-commit-config.yaml` (ruff + [gitleaks](https://github.com/gitleaks/gitleaks)
  para secretos, acotado a `src/copilot/`). Instalar con
  `uv run pre-commit install`.
*[EN]: **Pre-commit**: `.pre-commit-config.yaml` (ruff + gitleaks for
  secrets, scoped to `src/copilot/`). Install with `uv run pre-commit install`.*
- **Dependabot**: `.github/dependabot.yml`, ecosistemas `pip` y `github-actions`.
- **`SECURITY.md`**: datos sintéticos, advertencia de `joblib.load()`,
  guardrail de prompt injection como best-effort.
*[EN]: **`SECURITY.md`**: synthetic data, `joblib.load()` warning,
  prompt-injection guardrail as best-effort.*
- **Branch protection de GitHub**: no se pudo configurar desde este entorno
  (sin `gh`/token) — checklist manual en `DECISIONS.md` D29.
*[EN]: **GitHub branch protection**: couldn't be configured from this
  environment (no `gh`/token) — manual checklist in `DECISIONS.md` D29.*

Detalle completo en `DECISIONS.md` D29.


---


## Appendix: the underlying data and ML pipeline (Parte 1-5)

The original technical-assessment pipeline the Copilot's tools are built on
— real, tested, documented code, not superseded by the Copilot layer above.

### Arrancar la API de reclamaciones (Parte 4)
*Start the complaint-classification API*

```bash
export MOCK_LLM=1
uvicorn src.parte4_api.main:app --reload --port 8000
curl -s http://localhost:8000/health | python -m json.tool
```

### Ejecutar eval del clasificador de reclamaciones (golden set)
*Run the complaint classifier eval (golden set)*

```bash
MOCK_LLM=1 uv run python -m scripts.evaluate_classifier
#outputs/eval_report.json — accuracy, churn_threat recall, prompt-injection
#detection rate, retrieval category-precision@k. Ver DECISIONS.md D21.
```

### Ejecutar Parte 1 (genera artefactos en outputs/)
*Run Part 1 (generates artifacts in outputs/)*

```bash
uv run python -m src.parte1_pandas data/transactions_sample.csv
#outputs/monthly_kpis.csv, quality_report.json,merchants_at_risk.csv
```

### Ejecutar Parte 1 · PySpark rewrite (genera Delta tables en outputs/delta/)
*Run Part 1 · PySpark rewrite (generates Delta tables in outputs/delta/)*

Same 4 functions as `parte1_pandas.py`, rewritten with the PySpark DataFrame
API and a Delta Lake write, running locally via `delta-spark` (no cluster
needed — requires Java 11/17/21).

```bash
uv sync --extra pyspark
uv run --extra pyspark python -m src.parte1_pyspark data/transactions_sample.csv
#outputs/delta/{transactions_clean,monthly_kpis,merchants_at_risk}, monthly_kpis_spark.csv, quality_report_spark.json, merchants_at_risk_spark.csv
```

A Databricks Community Edition notebook covering the same
ingestion -> transform -> Delta write flow is at
`notebooks/databricks_parte1_pipeline.py` (Databricks source format — import
via Repos or Workspace > Import).

Tradeoffs between the pandas and PySpark implementations are documented in
DECISIONS.md ("Parte 1b · PySpark rewrite").

### Ejecutar notebook de ML (Parte 3)
*Run ML notebook (Part 3)*

```bash
uv run jupyter lab src/parte3_modeling.ipynb
#outputs/metrics.json,model.pkl,feature_importance.csv, model_card.md
```

Este es el mismo `model.pkl` que el Risk agent del Copilot puntúa en vivo —
ver `DECISIONS.md` D24 para el feature engineering portado y un hallazgo
honesto sobre su comportamiento en inferencia por-merchant.
*[EN]: This is the same `model.pkl` the Copilot's Risk agent scores live —
see `DECISIONS.md` D24 for the ported feature engineering and an honest
finding about its per-merchant inference behavior.*


## Estructura del proyecto
*Project structure*

```
├── DECISIONS.md         # 29 decisiones técnicas documentadas / 29 technical decisions documented
├── ASSUMPTIONS.md       # 3 ambiguedades identificadas en el diseño del pipeline
├── SELF_REVIEW.md       # 5 problemas honestos de la solución
├── SECURITY.md          # datos sintéticos, pickle warning, guardrails / synthetic data, pickle warning, guardrails
├── TOOLS_USED.md
├── src/
│   ├── copilot/                 # Merchant Intelligence Copilot — orquestador multi-agente
│   │   ├── api.py               #   POST /ask, GET /health
│   │   ├── graph.py             #   LangGraph: nodos + build_graph() + pick_next()
│   │   ├── router.py            #   route_mock() / route_real()
│   │   ├── synthesis.py         #   synthesize_mock() / synthesize_real()
│   │   ├── retrieval_core.py    #   vector store compartido (también usado por parte4_api/retrieval.py)
│   │   └── tools/                #   data_analyst.py, risk.py, grounding.py, complaint_classifier.py
│   ├── parte1_pandas.py        #4 funciones implementadas
│   ├── parte1_pyspark.py       # mismas 4 funciones, PySpark DataFrame API + Delta Lake
│   ├── parte2_sql.sql          #Q1-Q4 en Spark SQL
│   ├── parte3_modeling.ipynb   #pipeline LightGBM ejecutado — outputs/model.pkl usado en vivo por copilot/tools/risk.py
│   ├── parte4_api/             #FastAPI + Agno agent (mock + real) + RAG retrieval — reusado por copilot/tools/complaint_classifier.py
│   ├── parte5_bonus.py         #stub + analisis en DECISIONS.md D12
│   └── eda/eda.ipynb          # EDA completo con deteccion de trampas
├── notebooks/
│   └── databricks_parte1_pipeline.py  # notebook Databricks (Community Edition)
├── data/
│   ├── transactions_sample.csv        # ~200k filas, no versionado / not versioned (.gitignore)
│   ├── copilot_fixture_transactions.csv  # 222 filas, versionado, usado por tests/eval del copilot
│   └── policy_docs.json               # 15 documentos de política sintéticos (RAG del Grounding agent)
├── scripts/
│   ├── evaluate_classifier.py  # golden-set eval del clasificador de reclamaciones (DECISIONS.md D21)
│   ├── evaluate_copilot.py     # golden-set eval del copilot (DECISIONS.md D28)
│   ├── check_eval_floors.py    # gate de CI sobre ambos reportes (DECISIONS.md D29)
│   └── generate_copilot_fixture.py  # generador determinístico del fixture de arriba
├── tests/
│   ├── test_solution.py         #22 tests - Parte 1 pandas
│   ├── test_parte1_pyspark.py   #17 tests - Parte 1 PySpark rewrite (requiere --extra pyspark)
│   ├── test_api.py             #7 tests - Parte 4
│   ├── test_agent_adapter.py   #4 tests - adaptador Agent real de Agno
│   ├── test_retrieval.py       #15 tests - RAG retrieval (Parte 4b)
│   ├── test_eval.py            #7 tests - eval harness del clasificador (Parte 4b)
│   ├── test_bonus.py           #1 test  - Parte 5 (stub)
│   └── test_copilot_*.py       #82 tests - Copilot (data_analyst, risk, grounding, complaint_classifier, router, graph, synthesis, api, eval)
└── outputs/
    ├── monthly_kpis.csv, quality_report.json, merchants_at_risk.csv
    ├── monthly_kpis_spark.csv, quality_report_spark.json, merchants_at_risk_spark.csv
    ├── delta/                  # transactions_clean, monthly_kpis, merchants_at_risk (git-ignored)
    ├── eval_report.json          # golden-set eval clasificador (DECISIONS.md D21)
    ├── eval_report_copilot.json  # golden-set eval copilot (DECISIONS.md D28)
    ├── metrics.json, model.pkl, feature_importance.csv, model_card.md
```


## Resumen de resultados
*Results summary*

| Componente / Component | Estado / Status | Métricas clave / Key metrics |
|---|---|---|
| Copilot (orquestador multi-agente) | Corre end-to-end / Runs end-to-end | `POST /ask` — route 100% exact-match, citation hallucination 0%, risk-caveat mention 100% (11-example golden set). Ver DECISIONS.md D22-D29 |
| 1 · Pandas | Completo | 6 problemas de calidad detectados (5 trampas + 1 adicional) |
| 2 · SQL | Completo (+ ejecutado de verdad por el Data Analyst agent) / Completo (+ actually executed by the Data Analyst agent) | Q1-Q4 con partition pruning explicado; adaptado a esquema real en `copilot/tools/data_analyst.py` (D23) |
| 3 · ML | Ejecutado / Executed | ROC-AUC 0.58, PR-AUC 0.11 (sin leakage); puntuado en vivo por el Risk agent (D24) |
| 4 · API | Arranca / Starts | `/health` 200, mock + real Agent, 56 tests passing (73 con `--extra pyspark`); reusado por el Complaint classifier agent (D25) |
| 5 · Bonus! | Stub + análisis | Ver DECISIONS.md D12 |

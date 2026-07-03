# Merchant Intelligence Hub
#### Payne, Anna

A data pipeline and risk-scoring project for merchant transaction data: ingestion, cleaning, KPI/SQL analysis, a churn-risk ML model, and a FastAPI service on top. Originally started as a technical assessment for the Getnet AI Lab Graduate Program, and extended independently since because the problem was worth continuing to build on.


## Setup rápido
*Quick setup*

```bash
# Instalar dependencias (Python 3.10-3.13 , uv requerido)
uv sync --extra dev
# Sanity check del entorno
uv run python -c "import pandas, sklearn, fastapi, uvicorn, agno, pydantic; print('OK · environment ready')"
```

> **Nota Python 3.13**: `pandas` fue bumpeado de 2.2.2 a 2.2.3 (patch release, API idéntica) para compatibilidad con Python 3.13.

Atajos disponibles vía `Makefile` (`make help` para la lista completa):
*[EN]: Shortcuts available via `Makefile` (`make help` for the full list):*

```bash
make setup      # uv sync --extra dev
make test       # pytest -v con MOCK_LLM=1
make run        # uvicorn con MOCK_LLM=1
make lint       # ruff check
```







## Arrancar la API (Parte 4)
*Start the API*

```bash
# Opción A - sin clave OpenAI (LLM determinístico)       
export MOCK_LLM=1      
uvicorn src.parte4_api.main:app --reload --port 8000     

# Opción B -  con clave OpenAI propia
export OPENAI_API_KEY=sk-...
uvicorn src.parte4_api.main:app --reload --port 8000
```
    

Verificar que responde:   
```bash
curl -s http://localhost:8000/health | python -m json.tool    
```
     






## Ejecutar tests   
*Run tests*  

```bash
MOCK_LLM=1 uv run pytest tests/ -v
#31 passed  
```   


## Ejecutar Parte 1 (genera artefactos en outputs/)
*Run Part 1 (generates artifacts in outputs/)*      

```bash
uv run python -m src.parte1_pandas data/transactions_sample.csv
#outputs/monthly_kpis.csv, quality_report.json,merchants_at_risk.csv      
```



## Ejecutar Parte 1 · PySpark rewrite (genera Delta tables en outputs/delta/)
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


## Ejecutar notebook de ML (Parte 3)
*Run ML notebook (Part 3)*

```bash
uv run jupyter lab src/parte3_modeling.ipynb
#outputs/metrics.json,model.pkl,feature_importance.csv, model_card.md
```





## Estructura del proyecto
*Project structure*

```
├── DECISIONS.md         # 16 decisiones técnicas documentadas / 16 technical decisions documented
├── ASSUMPTIONS.md       # 3 ambiguedades identificadas en el diseño del pipeline
├── SELF_REVIEW.md       # 5 problemas honestos de la solución 
├── TOOLS_USED.md     
├── src/
│   ├── parte1_pandas.py        #4 funciones implementadas 
│   ├── parte1_pyspark.py       # mismas 4 funciones, PySpark DataFrame API + Delta Lake
│   ├── parte2_sql.sql          #Q1-Q4 en Spark SQL 
│   ├── parte3_modeling.ipynb   #pipeline LightGBM ejecutado 
│   ├── parte4_api/             #FastAPI + Agno agent (mock + real)
│   ├── parte5_bonus.py         #stub + analisis en DECISIONS.md D12    
│   └── eda/eda.ipynb          # EDA completo con deteccion de trampas  
├── notebooks/
│   └── databricks_parte1_pipeline.py  # notebook Databricks (Community Edition)
├── tests/ 
│   ├── test_solution.py         #17 tests - Parte 1 pandas
│   ├── test_parte1_pyspark.py   #17 tests - Parte 1 PySpark rewrite
│   └── test_api.py             #5 tests- Parte 4    (required)   
└── outputs/
    ├── monthly_kpis.csv
    ├── quality_report.json
    ├── merchants_at_risk.csv
    ├── monthly_kpis_spark.csv
    ├── quality_report_spark.json
    ├── merchants_at_risk_spark.csv
    ├── delta/                  # transactions_clean, monthly_kpis, merchants_at_risk (git-ignored)
    ├── metrics.json
    ├── model.pkl
    ├── feature_importance.csv
    └── model_card.md  


```




## Resumen de resultados
*Results summary*

| Parte / Part | Estado / Status | Métricas clave / Key metrics |
|---|---|---|
| 1 · Pandas | Completo  | 6 problemas de calidad detectados (5 trampas + 1 adicional) |
| 2 · SQL | Completo | Q1-Q4 con partition pruning explicado |
| 3 · ML | Ejecutado / Executed | ROC-AUC 0.58, PR-AUC 0.11 (sin leakage) |
| 4 · API | Arranca / Starts | `/health` 200, mock + real Agent, 31 tests passing (repo-wide) |
| 5 · Bonus! | Stub + análisis | Ver  DECISIONS.md D12 |

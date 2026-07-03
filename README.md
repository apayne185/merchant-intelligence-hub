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
│   ├── parte2_sql.sql          #Q1-Q4 en Spark SQL 
│   ├── parte3_modeling.ipynb   #pipeline LightGBM ejecutado 
│   ├── parte4_api/             #FastAPI + Agno agent (mock + real)
│   ├── parte5_bonus.py         #stub + analisis en DECISIONS.md D12    
│   └── eda/eda.ipynb          # EDA completo con deteccion de trampas  
├── tests/ 
│   ├── test_solution.py        #22 tests - Parte 1 
│   ├── test_api.py             #6 tests - Parte 4    (required)   
│   ├── test_agent_adapter.py   #2 tests - adaptador Agent real de Agno
│   └── test_bonus.py           #1 test  - Parte 5 (stub)
└── outputs/
    ├── monthly_kpis.csv
    ├── quality_report.json
    ├── merchants_at_risk.csv
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

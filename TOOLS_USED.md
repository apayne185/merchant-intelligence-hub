# TOOLS_USED.md    


---

## LLMs
*LLMs*

| Herramienta / Tool | Versión / modelo | Para qué la usé / What I used it for | % aproximado del código / Approximate % of code |
|---|---|---|---:|
| Claude Code (Anthropic) | claude-sonnet-4-6 | Setup del entorno, scaffolding inicial de funciones, creacion de los tablas en los README.mds, arreglar mis commentos en el code / Environment setup, initial function scaffolding, created the tables within README.md.s, fixed my comments in the code (they were messy) | ~20% (Partes 1-5) |
| Claude Code (Anthropic) | claude-sonnet-5 | Build de `src/copilot/` (agente completo): orquestador LangGraph, las 4 tools, la API, el golden-set eval, y las protecciones del repo, dirigido por mí en una sesión agéntica — arquitectura, alcance y cada decisión de diseño (framework, qué queda fuera, cómo se presenta el hallazgo de D24) fueron mías, revisadas y aprobadas en cada milestone, no generadas y aceptadas ciegamente / Build of `src/copilot/` (the whole agent): LangGraph orchestrator, the 4 tools, the API, the golden-set eval, and the repo protections, directed by me in an agentic session — architecture, scope, and every design decision (framework choice, what's out of scope, how the D24 finding is presented) were mine, reviewed and approved at each milestone, not generated and blindly accepted | ~90% (`src/copilot/` specifically; resto del repo sin cambios de esta fila / rest of the repo unchanged from the row above) |
| Claude Code (Anthropic) | claude-sonnet-5 | `Dockerfile` + `terraform/` (despliegue AWS del Copilot) — mismo patrón: yo decidí el alcance (solo Copilot, sin RDS/S3, estado local) antes de que se escribiera código; verificación real, no solo "compiló" (build + run + curl desde la shell del host, no `docker exec`) / `Dockerfile` + `terraform/` (AWS deployment of the Copilot) — same pattern: I decided scope (Copilot-only, no RDS/S3, local state) before any code was written; real verification, not just "it compiled" (build + run + curl from the host shell, not `docker exec`) | 100% of new files (`Dockerfile`, `terraform/*`) |

> El codigo generado fue revisado, adaptado y validado por mí. Los conteos reales (197913 filas con formato BR, 4182 duplicados, AUC = 0.58 ) son output de ejecuciones reales de el código, no inventados por el LLM
> *All of the generated code was reviewed, adapted and validated by me. The real counts (197913 rows in BR format, 4182 duplicates, AUC= 0.58) are output from the actual code runs and not invented/hallucinated by the LLM.*
>
> Mismo principio para `src/copilot/`: el hallazgo de D24 (el modelo de churn puntúa al merchant "de riesgo" del fixture más bajo que al sano) es un resultado real de correr `score_merchant()`, no algo que pedí o edité — lo dejé documentado tal cual salió porque es honesto, no porque fuera conveniente.
> *Same principle for `src/copilot/`: the D24 finding (the churn model scores the fixture's "at risk" merchant lower than the healthy one) is a real result from running `score_merchant()`, not something I asked for or edited — left documented as it came out because it's honest, not because it was convenient.*
>
> El Terraform nunca se aplicó contra una cuenta AWS real (sin credenciales en el entorno) — dicho explícitamente en `terraform/README.md`, no implicado como si estuviera en producción.
> *The Terraform was never applied against a real AWS account (no credentials in the environment) — stated explicitly in `terraform/README.md`, not implied as if it were live.*


---



## IDE / editor

- **Editor**: VS Code 
- **Plugins relevantes / Relevant plugins**:  Jupyter, Python extension, GitLens, Claude Code



---



## Librerías añadidas a `pyproject.toml`

| Librería | Versión  | Por que la añadí  |
|---|---|---|
| `pandas` (bump) | 2.2.2 to 2.2.3 | porque pandas 2.2.2 no tiene wheel para Python 3.13 / pandas 2.2.2 has no wheel for Python 3.13 |
| `langgraph` | >=1.2,<2 | orquestador del Merchant Intelligence Copilot — grafo/estado/control de flujo; las llamadas a LLM dentro de cada nodo siguen usando Agno (ya presente), sin segundo framework de agentes conviviendo. Ver DECISIONS.md D22/D26 / Merchant Intelligence Copilot orchestrator — graph/state/control-flow; LLM calls inside each node still go through Agno (already present), no second agent framework alongside it. See DECISIONS.md D22/D26 |
| `duckdb` | >=1.5,<2 | ejecución SQL real (parametrizada, no generada por LLM) del Data Analyst tool sobre un DataFrame en memoria. Ver DECISIONS.md D23 / real (parameterized, not LLM-generated) SQL execution for the Data Analyst tool over an in-memory DataFrame. See DECISIONS.md D23 |
| `pre-commit` | 4.0.1 | corre `.pre-commit-config.yaml` (ruff + gitleaks) localmente antes de cada commit. Ver DECISIONS.md D29 / runs `.pre-commit-config.yaml` (ruff + gitleaks) locally before each commit. See DECISIONS.md D29 |

El resto del starter original (pandas, sklearn, lightgbm, shap, agno, fastapi, pydantic) ya incluía todo lo necesario para las Partes 1-5; las 3 librerías de arriba son específicas de la capa del copilot (`src/copilot/`) añadida después.
*The rest of the original starter (pandas, sklearn, lightgbm, shap, agno, fastapi, pydantic) already included everything needed for Parts 1-5; the 3 libraries above are specific to the copilot layer (`src/copilot/`) added afterward.*



---

## Documentacion / recursos consultados   
*Documentation/resources *

- [pandas docs - `drop_duplicates`](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.drop_duplicates.html): para confirmar comportamiento NaN en subset deduplicado / confirm NaN behavior in deduplcation subset

- [LightGBM docs - `scale_pos_weight`](https://lightgbm.readthedocs.io/en/latest/Parameters.html#scale_pos_weight): para el parámetro de balance de clases / for the class balance parameter/

- [SHAP docs - TreeExplainer](https://shap.readthedocs.io/en/latest/generated/shap.TreeExplainer.html) : para compatibilidad con pipelines sklearn / compatibility with the sklearn pipelines

- [FastAPI docs - dependency_overrides](https://fastapi.tiangolo.com/advanced/testing-dependencies/) : para el patron mock en tests / for the mock pattern in tests


- [Agno source code](https://github.com/agno-agi/agno):  la documentación pública es escasa, inspeccioné el código fuente para entender `response_model` y decorator `@tool` / the public documentation is scarce, I inspected source code to understand `response_model` and the `@tool` decorator


---





## Reflexión rápida 

*Quick reflection* 

El LLM fue util para generar el scaffolding inicial rápidamente (estructura de funciones, boilerplate de tests) y para depurar errores específicos (comportamiento de `pd.NA` vs `np.nan` con `.astype(float)` de pandas 2.2.x). Donde no ayudó tanto fue en las decisiones de diseño de alto nivel cuando usar PR-AUC vs ROC-AUC, por qué el ROC-AUC de 0.58 es honesto y no señal de error, qué trampas buscar en los datos. Esas decisiones me requirieron leer el enunciado con cuidado y entender el dominio de acquiring.         

*The LLM was useful for quickly generating the initial project scaffolding (function structure, test boilerplate) and for debugging specific errors (the behavior of `pd.NA` vs `np.nan` with `.astype(float)` in pandas 2.2.x). Where it wasn't as helpful was within any high-level design decisions, when to use PR-AUC vs ROC-AUC, why the ROCAUC of 0.58 is honest and not sign of error, or  what traps to look for in the data. These decisions required me to carefully read the brief and understanding the acquiring domain.*    

Detecté un caso cuando el LLM propuso usar `.astype(float)` sobre una columna con `pd.NA` (nullable pandas NA),  lo cual falla en tiempo de ejecución con `TypeError: float() argument must be a string or a real number, not 'NAType'`. Lo detecté al ejecutar el codigo y usé `.where()` como fix correcto.  

      
*I detected a case where the LLM attempted using `.astype(float)` directly on a col with `pd.NA`, which will fail at runtime with `TypeError: float() argument must be a string or a real number, not 'NAType'`. I detected this when running the code and I used `.where()` as the correct fix.*


*I also used the LLM to triple check my translation abilities! I asked it to return to me a list of inccorect/inaccuracies within my ES-EN translations*

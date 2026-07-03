# TOOLS_USED.md    


---

## LLMs
*LLMs*

| Herramienta / Tool | Versión / modelo | Para qué la usé / What I used it for | % aproximado del código / Approximate % of code |
|---|---|---|---:|
| Claude Code (Anthropic) | claude-sonnet-4-6 | Setup del entorno, scaffolding inicial de funciones, creacion de los tablas en los README.mds, arreglar mis commentos en el code / Environment setup, initial function scaffolding, created the tables within README.md.s, fixed my comments in the code (they were messy) | ~20% |

> El codigo generado fue revisado, adaptado y validado por mí. Los conteos reales (197913 filas con formato BR, 4182 duplicados, AUC = 0.58 ) son output de ejecuciones reales de el código, no inventados por el LLM
> *All of the generated code was reviewed, adapted and validated by me. The real counts (197913 rows in BR format, 4182 duplicates, AUC= 0.58) are output from the actual code runs and not invented/hallucinated by the LLM.*


---



## IDE / editor

- **Editor**: VS Code 
- **Plugins relevantes / Relevant plugins**:  Jupyter, Python extension, GitLens, Claude Code



---



## Librerías añadidas a `pyproject.toml`

| Librería | Versión  | Por que la añadí  |
|---|---|---|
| `pandas` (bump) | 2.2.2 to 2.2.3 | porque pandas 2.2.2 no tiene wheel para Python 3.13 / pandas 2.2.2 has no wheel for Python 3.13 |

No añadí librerías nuevas. El starter ya incluye todas las necesarias (lightgbm, shap,agno, fastapi, pydantic)
*I didn't add any new libraries, the starter already include all necessary ones (lightgbm, shap, agno, fastapi,pydantic).*



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

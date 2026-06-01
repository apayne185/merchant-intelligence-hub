# DECISIONS.md — Payne, Anna

> **Regla**: por cada decisión técnica relevante responde **4 preguntas**:
> *[EN]: Rule: for each relevant technical decision answer 4 questions:*
> 1. **Qué hice** (acción concreta, no descripción genérica).
> 2. **Por qué** (criterio o evidencia, no "best practice").
> 3. **Qué descarté** (alternativas que consideraste y por qué no).
> 4. **Qué supuse** (supuesto que verificarías con un stakeholder real).


---




## Parte 1 · Pandas

### D1 · Tratamiento de tipos en `load_clean`
*Type handling in `load_clean`*

- **Qué hice**: Cargue el CSV con `dtype=str` y convertí explícitamente cada columna. Para el columna `amount`, quité el separador de miles (.), reemplacé ls coma decimal por punto, y despues apliqué `pd.to_numeric(errors='coerce')`. `transaction_date`: dos pasadas, primero `dayfirst=False` para el 90% YYYY-MM-DD datos, luego `dayfirst=True` en las filas que quedaron NaT  (el 10% DD/MM/YYYY = trampa T4). `merchant_id`/`transaction_id` a `Int64` nullable. Use categoricals de baja cardinalidad para ahorrar mas memoria

- *What I did: Loaded the CSV with `dtype=str`  and then explicitly converted each column. For column `amount`, I removed the thousands separator (.), replaced the decimal comma with periods, and then applied `pd.to_numeric(errors='coerce')`. For `transaction_date`, I did two passes, the first was `dayfirst=False` for the 90% YYYY-MM-DD data, then applied `dayfirst=True` on any rows that remain NaT (the 10% DD/MM/YYYY = trap T4). Last, `merchant_id`/`transaction_id` to nullable `Int64`. Categoricals for the low cardinality columns in order to save memory.* 


- **Por que**:El EDA detectó 197913 filas con coma decimal en `amount`, `pd`.  `read_csv` las infiere como un string sin errores, pero cualquier suma devuelve una 0 silenciosamente. El parseo en dos pasadas es la unica forma vectorizada de manejar dos formatos en la misma columna sin iterar fila a fila.   

- *Why*: The EDA detected 197,913 rows with a decimal comma in `amount`, `pd`.`read_csv` assumes them to be strings without error, but any sum then silently returns 0. The two-pass parsing is the only vectorized way to handle two formats in the same column without row-by-row iteration.*


- **Qué descarté**: `pd.read_csv(..., decimal=',',thousands='.')`  no funciona cuando la misma columna mezcla los vacíos (~3% NaN= trampa T3b). `dateutil.infer_datetime_format` es mas lento y tambien menos predecible con mezcla de formatos. Regex, fila a fila, viola el requisito de vectorizción.
- *What I discarded: `pd.read_csv(..., decimal=',',thousands='.')` does not work when the same column mixes empty values (~3% NaN = trap T3b). `dateutil.infer_datetime_format` is slower and also less predictable with any mixed formats. Row-by-row regex will violates the vectorization requirement.*

- **Qué supuse**: Que el separador de miles es siempre (.) y el decimal siempre es (,) (el formato de BR/ES). For ejemplo, si Getnet opera en México con un formato diferente, habría que parametrizar el parser. Yo verificaría el locale del sistema de caja con el equipo de ingeniería de datos.
- *What I assumed: That the thousands separator is always . and the decimal always , (BR/ES format). If Getnet would operates in Mexico with a different format, the parser would need to be parameterized. I would need to verify the POS system locale with the data engineering team.*

---




### D2 · Estrategia de deduplicación
*Deduplication strategy*

- **Qué hice**: Imputé `amount` nulo con mediana por segmento antes de deduplicar. Luego yo eliminé las filas con `drop_duplicates(subset=["merchant_id","transaction_date","amount","status", "channel"], keep="first")`. El resultado fue 4351 duplicados eliminados de 204000 filas (casi 2.1% = coherente con la trampa T5)

- *What I did: Imputed null `amount` with median by segment before deduplicating. Then I went to remove rows with `drop_duplicates(subset=["merchant_id","transaction_date","amount","status", "channel"], keep="first")`. The result was 4351 duplicates removed from 204000 rows (about 2.1% = consistent with trap T5) .*


- **Por que**: La trampa T5 genera duplicados con `transaction_id` distinto pero el resto identico, deduplicar por `transaction_id` no los captura. Imputar antes importa porque `NaN != NaN` en pandas, porque dos filas idénticas con `amount=NaN` no serían reconocidas como duplicadas sin imputación previa.

- *Why: Trap T5 will generate duplicates with a different `transaction_id` but an identical rest, as deduplicating by `transaction_id` does not catch them. Imputing here first matters because `NaN != NaN` in pandas, here two identical rows with `amount=NaN` would not be recognized as duplicates without prior imputation.*  

- **Qué descarté**: Deduplicar solo por `transaction_id` no captura T5. Hash de todas las columnas, una`transaction_id` diferente las haría distintas, es el mismo problema   
- *What I discarded: Deduplicating only by thhe `transaction_id` does not catch T5. Hashing of all columns means a different `transaction_id` would make them distinct, same problem.*

- **Qué supuse**: Que dos transacciones con los mismos merchant, fecha, importe, estado y canal son siempre duplicados y no compras legítimas distintas. En POS real podría haber dos compras idénticas en el mismo minuto. Tendria a pregunatr si hay granularidad de tiempo disponible 
- *What I assumed: two transactions with the same merchant, date, amount, status and channel are always duplicates not distinct legitimate purchases. In real POS there could be two identical purchases in the same minute. I have to would ask whether time granularity is available.*

---




### D3 · Heurística de `merchants_at_risk`
*`merchants_at_risk` heuristic*

- **Que hice**: Score compuesto ponderado de tres señales normalizadas en  [0,1]:
  - **TPV drop** (45%): `1 - minmax(tpv_30d / tpv_mediano_mensual)`, una caída relativa de volumen.
  - **Low approval rate** (35%): `1 - minmax(approval_rate_30d)`.
  - **Recent complaint** (20%): 1 si `last_complaint_date` en los últimos 30 días y si ≤ `reference_date`.


- *What I did: Weighted composite score of three signals normalized within [0, 1]:*
  - *TPV drop (45%): `1 - minmax(tpv_30d / monthly_median_tpv)`, a relative volume drop.*
  - *Low approval rate  (35%): `1 - minmax(approval_rate_30d)`.*
  - *Recent complaint (20%): 1 if `last_complaint_date` within  last 30 days and if ≤ `reference_date`*


- **Por que**: El EDA confirmó que estos 3 factores tienen correlación con churn. La caída de TPV es la señal mass directa de desactivación pre-churn (peso 45%). Tasa de aprobación baja indica a problemas técnicos que frustran al merchant (35%). Queja reciente es el insatisfacción inmediata (20%). Las ponderaciones son estimacione, en producción se estimarían con importancias SHAP del modelo completo de la Parte 3.

- *Why: The EDA confirmed that those 3 factors correlate with churn. TPV drop is the most direct signal of pre-churn deactivation (weight 45%). Low approval rate indicate technical issues frustrating the merchant (35%). Recent complaint is immediate dissatisfaction (20%). Weights are estimates, in production they would be estimated with SHAP importances from full Part 3 model.*

- **Qué descarté**: Score basado únicamente en TPV (demasiado simple). Usar directamente el score del modelo ML hubiera solapado con la Parte 3. Percentiles absolutos son menos interpretable que el ratio relativo al histórico del propio merchant.
- *What I discarded: Score based solely on TPV (too simple). Using the ML model score directly would have overlapped with Part 3. Absolute percentiles are less interpretable than the ratio relative to the merchant's own history.*

- **Que supuse**: Que los últimos 30 daas son la ventana relevante para señales debiles. Si el ciclo de intervención del equipo es de 14 días, habría que ajustar la ventana.       
- *What I assumed: that the last 30 days would be the relevant window for weak signals. If the teams intervention cycle is 14 days, the window would need to be adjustd*

---




## Parte 3 · Modelado ML
*ML Modelling*

### D4 · Features descartadas (trampas detectadas + razones)
*Discarded features (detected traps + reasons)*

- **Qué hice**: Excluí explícitamente del modelo:
- *What I did: Explicitly excluded from the model:*

  | Feature | Razón / Reason |
  |---|---|
  | `cancellation_reason` | **T1 (leakage directo / direct leakage)**: rellena en ~92% de churners vs 0% de no-churners / filled in ~92% of churners vs 0% of non-churners. El modelo aprendería el target directamente / The model would learn the target directly. |
  | `last_complaint_date` raw | **T2 (leakage temporal / temporal leakage)**: 3.454 filas tienen fecha posterior a `reference_date` — información del futuro / 3,454 rows have a date after `reference_date` — future information. Derivé `days_since_complaint` capado a `reference_date` / Derived `days_since_complaint` capped at `reference_date`. |
  | Transactions con `transaction_date > reference_date` | **Trampa no documentada / Undocumented trap**: el CSV incluye transacciones Oct-Dic 2025 / the CSV includes Oct-Dec 2025 transactions. Un merchant con actividad futura claramente no está churning / A merchant with future activity is clearly not churning — filtrar estas filas en features produce leakage implícito / filtering these rows in features produces implicit leakage. |
  | `reference_date` | Constante en todo el dataset / Constant across the dataset |
  | `transaction_id` | Surrogate key sin valor predictivo / Surrogate key with no predictive value |   


- **Por que**: Si yo incluyo cualquiera de las tres primeras, el modelo obtendría un AUC cercano a 1.0 pero fallaría en producción donde esos datos no existen en el momento de prediccion.     

- *Why: If I include any of first three, the model would achieve AUC close to 1.0 but would fail in production where that data does not exist at prediction time*          


- **Qué descarté**: Incluir `cancellation_reason` con imputación "desconocido", todavia sigue siendo leakage porque la ausencia del valor es informativa del target. Si usas `last_complaint_date` como numérico sin capado, filtra información futura para ~3500 churners.      
- *What I discarded: Including `cancellation_reason` with "unknown" imputation, still has leakage because the absence of the value is informative of the target. Using `last_complaint_date` as a number without capping would leaks the future information for ~3500 churners.*

- **Que supuse**: Que `reference_date` es el punto exacto de predicción en producción. Si el modelo se ejecuta con lag de N días, habría que ajustar el ventana de features.
- *What I assumed: That `reference_date` is the exact prediction point in production. If the model runs with a lag of N days, the feature window would then need to be adjusted.*





---


### D5 · Split temporal aware y prevención de leakage
*Temporal aware split and leakage prevention*

- **Que hice**: Split estratificado 80/20 de `merchant_id` (7986 train/1996 test). No hice un split temporal por snapshot porque este dataset tiene una sola `reference_date` (2025-09-30), no hay multiples snapshots disponibles para simular un evaluación temporal real. Riesgo de leakage temporal se mitigó íntegramente en la ingeniería de features, todas las agregaciones usan solo `transaction_date <=reference_date`

- **Por qué**: Con un unico snapshot, el split temporal clásico no aplica, pero el split por merchant garantiza que no hay contaminación de datos entre train y test. La estratificación preserva el 8.75 de churn en los dos splits
- *Why: With only a single snapshot, the classic temporal split doesn't apply. However, merchant split guarantees no data contamination between train and test. Stratification preserves the 8.75% churn rate in both splits*

- **Qué descarté**:Split de `transaction_date`, poque el target es por merchant, no por transacción. Y `TimeSeriesSplit` de sklearn porque requiere múltiples snapshots temporales del target.
- *What I discarded: Split by `transaction_date` because the target is per merchant, not transaction. sklearn's `TimeSeriesSplit` as well, as it requires multiple temporal snapshots of the target.*

- **Qué supuse**: Que en producción habría multiples snapshots mensuales (un rolling window). Con mas de 3 snapshots implementaría rolling origin validation para estimar AUC fuera de muestra de forma mas robusta.
- *What I assumed: In production there would be many monthly snapshots (a rolling window). With more than 3 snapshots I would implement rolling origin validation so to estimate out-of-sample AUC more robustly.*



---

### D6 · Métricas y umbralización
*Metrics and thresholding*

- **Qué hice**: Reporté ROC-AUC=0.58, PR-AUC=0.11, Brier score=0.12, y recall@k (k=1%, 5%, 10%). No umbralicé en 0.5, caso de negocio de retención requiere rankear merchants para priorizar llamadas, no clasificar binario.
- *What I did: Reported ROC-AUC=0.58, PR-AUC=0.11, Brier score=0.12, and recall@k (k=1%, 5%, 10%). I didn't threshold at 0.5, the retention business case needs ranking merchants in order to prioritize calls, not binary classification.*

- **Por qué**: Con 8.75% de positivos, accuracy es casi inutil. PR-AUC es la métrica principal para imbalanced problems, porque mide la calidad del ranking en la región de alta precisión. Recall@10% indica cuántos churners capturaríamos si contactásemos al top-10% asi que es directamente accionable para un equipo de retención. El ROC-AUC de 0.58 es modesto,  sin features de leakage, predecir churn a 90 días desde una sola snapshot transaccional es un poco difícil.

- *Why: With the 8.75 positives, accuracy is basically useless. PR-AUC is the main metric for imbalanced problems, it measures ranking quality in the high precision region. Recall@10% indicates how many churners we would capture if we contacted the top-10%, therefore itsdirectly actionable for the retention team. The ROC-AUC of 0.58 is modes, without leakage features, predicting churn 90 days out from a single transactional snapshot is difficult.*

  

- **Qué descarté**: el F1 score depende del umbral elegido. Tambien, acuracy porque es engañosa con un imbalance. Un AUC > 0.95 habría sido señal de leakage y lo habría investigado antes de reportar.
- *What got discarded: F1 score, because it depends on the chosen threshold. Accuracy as well, because it is misleading with imbalance. An AUC > 0.95 would have been a leakage signal and I would have investigated before reporting.*

- **Qué supuse**: que equipo de retencion trabaja con listas ranked y puede asignar un budget de N llamadas cada semana. Si necesitan clasificación binaria hard, añadiría una calibracion isotónica y  elegiría el umbral maximizando F2 (recall pesa 2x que precision para retención).
- *What I assumed: retention team will be worknig with ranked lists and can assign a budget of N calls per week. If they need hard binary classification, I would add isotonic calibration and choose the threshold maximizing F2 (recall weighs 2x as much as precision for retention).*

---




## Parte 4 · FastAPI + Agno

### D7 · Por qué Agno (vs LangChain/LlamaIndex /código normal)
*Why Agno*

- **Qué hice**: Usé Agno, tal como especifica el enunciado. El scaffolding de `src/parte4_api/agent.py` ya incluía el mock funcional y el esqueleto de herramientas.
- *What I did: Used Agno, as specified by the brief. The scaffolding from `src/parte4_api/agent.py` already included the functional mock and the tools skeleton.*

- **Por qué**: Agno ofrece `response_model` Pydantic nativo (structured output forzado, sin JSON parsing manual), tools  que el agente puede invocar, e el integración directa con OpenAI. Esto elimina el punto de fallo más habitual en los pipelines agentic
- *Why: Agno offers native Pydantic `response_model` (forced structured output w/out manual JSON parsing),  tools the agent invoke, and also direct OpenAI integration. This removes the most common failure point within agentic pipelines.*




---

### D8 · Modelo elegido + estimación de coste a 5000 emails cada día
*Chosen model and cost estimate at 5000 emails a day*

- **Modelo / Model**:`gpt-4o-mini` en producción, mock determinístico cuando `MOCK_LLM=1`.

- **Tokens medios por request**:
  - Input: 300 tokens (system prompt ~200 + email ~100)
  - Output: 80 tokens (JSON estructurado)
  - Total: ~380 tokens/request

- **Coste por request** (precios OpenAI en mayo 2026 :
  - gpt-4o-mini: $0.15/1M input + $0.60/1M output     
  - Input: 300 × $0.00000015 = $0.000045    
  - Output: 80 × $0.00000060 = $0.000048   
  - Total: $0.000093/request

- **Coste mensual estimado**:
  - 5000 emails/dia × 30 dias = 150000 requests de mes 
  - 150000×$0.000093 ≈ $14/mes
  - Con buffer de retries y overhead (+20%): $17/mes



---




### D9 · Diseño del schema Pydantic
*Pydantic schema design*

- **Que hice**: `ClassifyResponse` con  `category: Category` (StrEnum cerrado de 6 valors), `urgency: conint(ge=1,le=5)`, `reasoning: str` (max_length=300), `requires_human_escalation: bool`, `merchant_context_used: bool`,  `latency_ms: int`
- *What I did:`ClassifyResponse` with `category: Category` (closed StrEnum of 6 values), `urgency: conint(ge=1,le=5)`, `reasoning: str` (max_length=300), `requires_human_escalation: bool`, `merchant_context_used: bool`,  `latency_ms: int`.*

- **Por qué enum cerrado de categorías**: Evita que el LLM invente categorías libres que el sistema de ticketing no reconocería, el enum fuerza al agente a elegir entre opcines predefinidas de `response_model`.
- *Why a closed category enum: Prevent the LLM from inventing free categories that the ticketing system does not recognize, the enum forces the agent to choose from predefined options of `response_model`.*

- **Por qué cap 300 chars en `reasoning`**: Suficiente para que un agente humano entienda la clasificación sin leyendo el email completo. Sin límite, es posible que el LLM genera respuestas de 2000+ tokens que inflan costes.
- *Why cap 300 chars in `reasoning`: It is enough for human agents to understand the classifcation without reading the full email. Without a limit, the LLM might generates 2000+ token responses that inflate costs without need*


---





### D10 · Estrategia de evaluación antes de producción
*Evaluation strategy before production*

- **Qué haría**:
  1. **Golden set**: 50 emails por categoría × 6 categorías = 300 emails minimo, etiquetados por los agentes de customer service (fuente de verdad operacional). Estratificado por idima (es/pt/en), y segmento
  2. **Métricas**: accuracy de categoria >85%, recall de `churn_threat` >90% (un falso negativo = merchant perdido sin intervencion), y tasa de detección de prompt injection = 100%.
  3. **LLM as judge**: usar gpt-4o para evaluar si el reasoning es coherente con el email y la categoría asignada. Esto coste: ~$0.001/evaluación.
  4. **Shadow mode**: desplegar en paralelo con el flujo actual durante 2 semanas, comparar categorías del modelo vs sistema legacy.

- *What I would do:*
  *1. Golden set: 50 emails per category × 6 categores = 300 emails minimum, labeled by the customer service agents (operational ground truth). Stratified by language (es/pt/en) and segment.*
  *2. Metrics: category accuracy > 85%, `churn_threat` recall > 90% (one false negative = lost merchant w/out intervention), prompt injection detection rate = 100%*
  *3. LLM as judge:use gpt-4o to evaluate whether the reasoning is consistent with email and the assigned category. Costs ~$0.001/ evaluation.*
  *4. Shadow mode: deploy in parallel with the current flow for 2 weeks, compare model categories vs legacy system.*

---

### D11 · Mitigación cuando el LLM falla (urgencia 5 clasificada como 2)
*Mitigation when the LLM fails (urgency 5 classified as 2)*

- **Qué hice**: Dos capas de seguridad: 
* El guardrail de prompt injection devuelve `requires_human_escalation=True` de forma determinista antes de llegar al LLM     
* La tool `flag_for_human_review` escribe en `outputs/human_review_queue.jsonl` cuando el agente detecta alta urgencia o churn threat,  permitiendo auditoría post-hoc. 

- *What I did: two safety layers:*
* *the prompt injection guardrail deterministically returns `requires_human_escalation=True` before reaching the LLM.* 
* *The `flag_for_human_review` tool writes to `outputs/human_review_queue.jsonl` when a agent detects high urgency or churn threat, allowing for post-hoc auditing.*

- **Por qué**: El LLM puede equivocar el numero exacto de urgency pero raramente va a clasificar `fraud` como `billing`. Las reglas de escalación basadas en `category` son mas robustas que confiar solo en el numero exacto de urgency.
- *Why:the LLM might get the exact urgency number wrong but will rarely classifies `fraud` as `billing`. Escalation rules based on `category` are more robust than simply relying on the exact urgency number.*

- **Qué descarté**: Umbral de confianza del LLM, porque no tiene calibración fiable. Ensemble de dos modelos porque duplica los costes. Tambien, rescoring de urgency con keywords post-hoc, añade complejidad sin garantías.
- *What I discarded: LLM confidence threshold, because it has no reliable calibration. Ensemble of two models doubles costs. Post-hoc urgency re-scoring with keywords adds complexity without guarantees.*


---





## Parte 5 · Pregunta-trampa (collusion rings)
*Trick question  (collusion rings)*

### D12 · Honestidad técnica
*Technical honesty*

- **Por que este problema es difícil**: Los anillos de colusión requieren detectar patrones de transacciones cruzadas entre merchants (A-B-C-A). Esto no es detectable con una analisis por merchant individual. En datos de acquiring, las transacciones son entre merchants y su clientes para detectar colusión real necesitaríamos el `cardholder_id` de cada transacción, para ver si el "cliente" de A es el propio B

- *Why is this problem is difficult: Collusion rings require detecting patterns of cross transactions between merchants (A-B-C-A). This is is not detectable with individual merchant analysis. When acquiring the data, transactions are between these merchants and their customers, to detect real collusion we would need the `cardholder_id` of each transaction to see if A's "customer" is truly B itself.*

- **Qué datos pedirías**: `cardholder_id` o `device_fingerprint` por transacción. Datos de onboarding (comparten dirección, teléfono, representante legal?). Series temporales de reciprocidad (A cobra a B que cobra a A en ventana corta).


- *What data I would request: `cardholder_id` or `device_fingerprint` per transaction. Onboarding data (share address, phone, legal representative?). Reciprocity time series (A charges B who charges A in a short window).*


- **Qué algoritmos investigaría**:
  1. **Proyección bipartita de grafo**: construir grafo merchant-cliente, proyectar sobre merchants, detectar comunidades con Louvain.
  2. **Detección de ciclos**: buscar ciclos de longitud 3+ con importes similares en ventana temporal < 72h.
  3. **Isolation Forest sobre pares**: features de par (A, B) — reciprocidad, frecuencia, similitud de importes.

- *What algorithms you would investigate:*
  *1. Bipartite graph projection: builds merchant client graph, projects onto merchants, and detect communities with Louvain.*
  *2. Cycle detection: look for cycles of length +3, with similar amounts within a time window of 72h.*
  *3. Isolation Forest on pairs: pair features (A, B) (reciprocity, frequecy, amount similarity).*

- **Tiempo realista necesario**: 2-3 semanas con los datos correctos
  * 1 semana exploración + construcción del grafo
  * 1 semana algoritmos, 
  * 1 semana validacion con los casos conocidos del equipo de fraude 


- *Realistic time needed: 2-3 weeks with the correct data.*
  * *1 week exploration + graph construction*
  * *1 week algorithms*
  * *1 week validation with known cases from the fraud team.*



---






## Decisiones extra  
*Additional decisions*

### D13 · Bump de pandas 2.2.2 a 2.2.3

- **Qué hice**: Actualicé `pyproject.toml` de `pandas==2.2.2` a `pandas==2.2.3`.
- **Por qué**: el pandas 2.2.2 no tiene wheel precompilado para Python 3.13. `uv sync` intentaba compilar desde source y el fallaba en el paso de Meson/Cython.  pandas 2.2.3 (patch release y API idéntica) si tiene wheel para Python 3.13. Y el evaluador en 3.11/3.12 no ve diferencia.
- Yo supuse que el evaluador tiene Python 3.11 o 3.12 y que el bump de patch version es transparente.
# DECISIONS.md — Payne, Anna

> Reasoning behind the pipeline design. For each relevant technical decision, **4 questions**:
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

- **Qué supuse**: Que el separador de miles es siempre (.) y el decimal siempre es (,) (el formato de BR/ES). Si el acquirer operase en un país con un formato diferente (ej. México), habría que parametrizar el parser. Yo verificaría el locale del sistema de caja con el equipo de ingeniería de datos.
- *What I assumed: That the thousands separator is always . and the decimal always , (BR/ES format). If the acquirer operated in a country with a different format (e.g. Mexico), the parser would need to be parameterized. I would need to verify the POS system locale with the data engineering team.*

---




### D2 · Estrategia de deduplicación
*Deduplication strategy*

- **Qué hice**: Imputé `amount` nulo con mediana por segmento antes de deduplicar. Luego yo eliminé las filas con `drop_duplicates(subset=["merchant_id","transaction_date","amount","status", "channel"], keep="first")`. El resultado fue 4182 duplicados eliminados de 204000 filas (casi 2.05% = coherente con la trampa T5)

- *What I did: Imputed null `amount` with median by segment before deduplicating. Then I went to remove rows with `drop_duplicates(subset=["merchant_id","transaction_date","amount","status", "channel"], keep="first")`. The result was 4182 duplicates removed from 204000 rows (about 2.05% = consistent with trap T5) .*


- **Por que**: La trampa T5 genera duplicados con `transaction_id` distinto pero el resto identico, deduplicar por `transaction_id` no los captura. Imputar antes importa para que el valor final de `amount` en los KPIs sea la mediana real y no NaN — pandas ya trata `NaN == NaN` como igual dentro de `duplicated()`/`drop_duplicates()` (a diferencia de `==` escalar), así que el orden no afecta si se detectan como duplicados, pero sí afecta qué valor de `amount` sobrevive.

- *Why: Trap T5 generates duplicates with a different `transaction_id` but an otherwise identical row, so deduplicating by `transaction_id` alone doesn't catch them. Imputing first matters so the final `amount` value in the KPIs is the real median, not NaN — pandas already treats `NaN == NaN` as equal inside `duplicated()`/`drop_duplicates()` (unlike scalar `==`), so the ordering doesn't affect whether they're detected as duplicates, but it does affect which `amount` value survives.*

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

## Parte 1b · PySpark rewrite — pandas vs. PySpark tradeoffs
*Same 4 functions (`load_clean`, `monthly_kpis`, `quality_report`, `merchants_at_risk`), rewritten with the DataFrame API in `src/parte1_pyspark.py`, run locally (`local[*]`) with `delta-spark`, no cluster needed.*

- **Qué hice**: Reimplementé las 4 funciones con el DataFrame API de PySpark en vez de pandas, manteniendo exactamente las mismas reglas de negocio (T1-T5, ventanas, pesos del score). Verifiqué numéricamente que ambas implementaciones producen los mismos KPIs y quality_report sobre `data/transactions_sample.csv` antes de dar el rewrite por terminado.
- *What I did: Reimplemented the 4 functions with PySpark's DataFrame API instead of pandas, keeping the exact same business rules (T1-T5, windows, score weights). I numerically verified both implementations produce the same KPIs and quality_report on `data/transactions_sample.csv` before considering the rewrite done.*

- **Diferencias encontradas al comparar output pandas vs. Spark**:
  1. **Mediana aproximada vs exacta**: usar `percentile_approx` para la mediana de `amount` por segmento (imputación de nulls) introduce ruido de punto flotante en el valor imputado. Como `amount` es parte de la clave de deduplicación (T5), ese ruido cambiaba qué filas se consideraban duplicados exactos, afectando el conteo. Cambié a `percentile` (exacto) para que el imputado coincida bit a bit con la mediana de pandas — con eso, KPIs y quality_report coinciden hasta ruido de suma en punto flotante (~1e-11), esperable por el orden de suma distribuida.
  2. **Empates en `merchants_at_risk`**: las distribuciones de `risk_score` son idénticas entre pandas y Spark (mismos cuantiles, mismos conteos por valor), pero ~135/200 merchants del top-200 caen en un empate exacto en 0.8. Ni pandas (`sort_values` sin criterio de desempate) ni un `orderBy` naive en Spark garantizan el mismo orden dentro de un empate masivo, así que el conjunto exacto de "top 200" difiere aunque el cálculo sea correcto en ambos. Añadí `merchant_id` ascendente como criterio de desempate secundario en Spark para que el resultado sea al menos reproducible entre corridas.
  3. **`keep='first'` no tiene equivalente real en Spark**: pandas' `drop_duplicates(keep='first')` conserva la primera fila en el orden de lectura del CSV. Spark no garantiza ningún orden de lectura estable entre particiones, así que como proxy usé "transaction_id más bajo por grupo" (ver comentario en `load_clean`, T5). Esto coincide con "primera fila del CSV" solo si `transaction_id` es monótono con el orden del archivo — supuesto razonable pero **no verificado** contra los datos. Si se genera un CSV donde eso no se cumpla, pandas y Spark podrían quedarse con filas de contenido idéntico en las columnas de dedup pero distintas en columnas no incluidas en la clave (`segment`, `mcc`, `cancellation_reason`, `fla_churn90`), lo cual no se detectaría con los tests actuales.
- *Differences found comparing pandas vs. Spark output: (1) Approximate vs exact median — using `percentile_approx` for the per-segment `amount` median (null imputation) introduces floating-point noise in the imputed value. Since `amount` is part of the dedup key (T5), that noise changed which rows counted as exact duplicates. Switched to exact `percentile` so the imputed value matches pandas' median bit-for-bit — after that, KPIs and quality_report agree up to distributed-summation floating point noise (~1e-11). (2) Ties in `merchants_at_risk` — risk_score distributions are identical between pandas and Spark (same quantiles, same per-value counts), but ~135/200 top-200 merchants land in an exact tie at 0.8. Neither pandas' `sort_values` (no tiebreak) nor a naive Spark `orderBy` guarantee the same order within a massive tie, so the exact top-200 set differs even though the computation is correct in both. Added ascending `merchant_id` as a secondary Spark sort key so the result is at least reproducible run-to-run. (3) `keep='first'` has no real Spark equivalent — pandas' `drop_duplicates(keep='first')` keeps the first row in CSV read order; Spark doesn't guarantee a stable read order across partitions, so I used "lowest transaction_id per group" as a proxy (see the T5 comment in `load_clean`). This only matches "first row in the CSV" if `transaction_id` happens to be monotonic with file order — a reasonable but **unverified** assumption. If a CSV were ever generated where that doesn't hold, pandas and Spark could keep rows identical on the dedup-key columns but different on non-key columns (`segment`, `mcc`, `cancellation_reason`, `fla_churn90`) — a divergence the current tests wouldn't catch.*

- **Cuándo usaría cada uno**: pandas para este volumen (204k filas) es más simple y rápido de iterar (sin overhead de JVM/sesión Spark). PySpark se justifica cuando el dataset no cabe en memoria de una máquina, o cuando el pipeline necesita correr sobre un cluster/Databricks como parte de una arquitectura de datos más amplia (un caso real en acquiring a esa escala). El rewrite aquí es una prueba de portabilidad del pipeline, no una necesidad de escala para este dataset de muestra.
- *When I'd use each: pandas is simpler and faster to iterate on for this volume (204k rows) — no JVM/Spark session overhead. PySpark is justified once the dataset doesn't fit in a single machine's memory, or the pipeline needs to run on a cluster/Databricks as part of a larger data architecture (a real case in acquiring at that scale). This rewrite is a portability exercise, not a scale necessity for this sample dataset.*

---




## Parte 2 · SQL
*SQL*

### D14 · Q1 — filtro doble sobre dat_process y transaction_date

- **Qué hice**: Filtré `transactions` tanto por `dat_process BETWEEN '2025-07-01' AND '2025-09-30'` (columna de partición) como por `transaction_date BETWEEN '2025-07-01' AND '2025-09-30'` (fecha de negocio).
- *What I did: Filtered `transactions` both by `dat_process BETWEEN '2025-07-01' AND '2025-09-30'` (partition column) and by `transaction_date BETWEEN '2025-07-01' AND '2025-09-30'` (business date).*

- **Por qué**: `dat_process` es la fecha de ETL/procesamiento, no necesariamente igual a `transaction_date` (una transacción del 30-sep podría procesarse el 1-oct). El filtro sobre `dat_process` habilita partition pruning grueso; el filtro sobre `transaction_date` asegura que el resultado sea correcto por fecha de negocio aunque haya lag entre ambas columnas cerca de un límite de trimestre.
- *Why: `dat_process` is the ETL/processing date, not necessarily equal to `transaction_date` (a Sep-30 transaction could be processed on Oct-1). Filtering on `dat_process` enables coarse partition pruning; filtering on `transaction_date` ensures the result is correct by business date even if there's lag between the two columns near a quarter boundary.*

- **Qué supuse**: Que `dat_process` y `transaction_date` coinciden en la gran mayoría de los casos (lag de 0-1 días). No verificado contra el schema real — lo confirmaría con el equipo de ingeniería de datos antes de confiar en el partition pruning como única garantía de completitud.
- *What I assumed: That `dat_process` and `transaction_date` coincide in the vast majority of cases (0-1 day lag). Not verified against the real schema — I'd confirm with the data engineering team before relying on partition pruning alone as a completeness guarantee.*

---

### D15 · Q1 — approval_rate: denominador incluye reversed

- **Qué hice**: `approval_rate = n_approved / COUNT(*)`, donde `COUNT(*)` cuenta todas las transacciones del periodo (approved + denied + reversed).
- *What I did: `approval_rate = n_approved / COUNT(*)`, where `COUNT(*)` counts all transactions in the period (approved + denied + reversed).*

- **Qué descarté**: Excluir `reversed` del denominador (solo `approved`/`denied`). Lo descarté porque una transacción `reversed` fue aprobada y luego revertida — sigue siendo relevante para medir qué fracción de los intentos de cobro del merchant resultan en TPV neto retenido.
- *What I discarded: Excluding `reversed` from the denominator (only `approved`/`denied`). Discarded because a `reversed` transaction was approved and then reversed — still relevant for measuring what fraction of the merchant's charge attempts result in retained net TPV.*

- **Qué supuse**: Que "approval_rate" en el contexto de negocio del acquirer incluye reversals en el denominador. Lo verificaría con el equipo de producto — ver también ASSUMPTIONS.md A1 sobre la misma ambigüedad en TPV.
- *What I assumed: That "approval_rate" in the business context includes reversals in the denominator. I'd verify this with the product team — see also ASSUMPTIONS.md A1 on the same ambiguity for TPV.*

---

### D16 · Q3 — self-join en vez de LAG(tpv, 12) para el YoY

- **Qué hice**: Uní `monthly_tpv` consigo misma por `(merchant_id, mo)` con `prev.yr = 2024`, en vez de usar `LAG(tpv, 12) OVER (PARTITION BY merchant_id ORDER BY month_start)`.
- *What I did: Joined `monthly_tpv` to itself on `(merchant_id, mo)` with `prev.yr = 2024`, instead of using `LAG(tpv, 12) OVER (PARTITION BY merchant_id ORDER BY month_start)`.*

- **Por qué**: `LAG(tpv, 12)` con offset fijo es sintaxis Spark SQL perfectamente válida — la elección no es por falta de soporte. El motivo real es que `monthly_tpv` puede tener huecos: si un merchant no tuvo transacciones en algún mes, ese mes no aparece como fila. `LAG(tpv, 12)` se desplazaría 12 *filas* hacia atrás en la partición, no 12 *meses* — con huecos, terminaría comparando meses que no son realmente el mismo mes del año anterior. El self-join empareja explícitamente por `mo`, así que es correcto independientemente de huecos en la serie.
- *Why: `LAG(tpv, 12)` with a fixed offset is perfectly valid Spark SQL — the choice isn't due to a lack of support. The real reason is that `monthly_tpv` can have gaps: if a merchant had no transactions in some month, that month doesn't appear as a row. `LAG(tpv, 12)` would shift 12 *rows* back within the partition, not 12 *months* — with gaps, it would end up comparing months that aren't actually the same month a year prior. The self-join matches explicitly on `mo`, so it's correct regardless of gaps in the series.*

- **Qué descarté**: `LAG(tpv, 12)` — funcionaría solo si se garantiza una fila por cada (merchant_id, mes) sin huecos, lo cual requeriría un join contra un "calendar spine" primero. El self-join es más simple para este caso.
- *What I discarded: `LAG(tpv, 12)` — would only work if every (merchant_id, month) combination is guaranteed a row with no gaps, which would require joining against a calendar spine first. The self-join is simpler for this case.*

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
  | `last_complaint_date` raw | **T2 (leakage temporal / temporal leakage)**: 3.535 filas tienen fecha posterior a `reference_date` — información del futuro / 3,535 rows have a date after `reference_date` — future information. Derivé `days_since_complaint` capado a `reference_date` / Derived `days_since_complaint` capped at `reference_date`. |
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

- **Que hice**: Split estratificado 80/20 de `merchant_id` (7973 train/1994 test). No hice un split temporal por snapshot porque este dataset tiene una sola `reference_date` (2025-09-30), no hay multiples snapshots disponibles para simular un evaluación temporal real. Riesgo de leakage temporal se mitigó íntegramente en la ingeniería de features, todas las agregaciones usan solo `transaction_date <=reference_date`

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





## Parte 4b · RAG retrieval — recuperación de casos históricos similares
*RAG retrieval — retrieval of similar historical cases*

### D17 · Vector store en el repo en vez de una base de datos vectorial real

- **Qué hice**: Implementé `SimpleVectorStore` (`src/parte4_api/retrieval.py`) — una clase pequeña con `add()`/`query()` que hace búsqueda por similitud coseno por fuerza bruta sobre un array de numpy en memoria, en vez de usar chromadb/FAISS/pgvector.
- *What I did: Implemented `SimpleVectorStore` (`src/parte4_api/retrieval.py`) — a small class with `add()`/`query()` doing brute-force cosine-similarity search over an in-memory numpy array, instead of using chromadb/FAISS/pgvector.*

- **Por qué**: El corpus (`data/historical_complaints.json`) tiene 42 registros. Una búsqueda vectorial por fuerza bruta sobre esa cantidad tarda microsegundos; una base de datos vectorial real añadiría una dependencia pesada (chromadb trae onnxruntime) para resolver un problema de escala que no existe todavía.
- *Why: The corpus (`data/historical_complaints.json`) has 42 records. Brute-force vector search over that count takes microseconds; a real vector database would add a heavy dependency (chromadb pulls in onnxruntime) to solve a scale problem that doesn't exist yet.*

- **Qué descarté**: chromadb — evalué usarlo por ser el más reconocible como "vector store" para un lector técnico, pero decidí que demostrar saber cuándo NO sobre-diseñar es una señal más fuerte que una dependencia extra sin necesidad real. FAISS — más ligero que chromadb, pero sigue siendo ceremonia innecesaria para 42 filas.
- *What I discarded: chromadb — considered using it since it's the most recognizable "vector store" to a technical reader, but decided that demonstrating knowing when NOT to over-engineer is a stronger signal than an unnecessary extra dependency. FAISS — lighter than chromadb, but still unnecessary ceremony for 42 rows.*

- **Qué supuse**: Que el corpus se mantendrá en el rango de cientos, no miles/millones, de casos históricos. Si esto creciera a producción real con miles de casos por día, migraría a pgvector (ya versionado, sin infra nueva si ya se usa Postgres) o a un servicio gestionado (Pinecone/Weaviate) con un índice HNSW/IVF para evitar la búsqueda O(n) de fuerza bruta.
- *What I assumed: That the corpus will stay in the hundreds, not thousands/millions, of historical cases. If this grew to real production scale with thousands of cases a day, I'd migrate to pgvector (already versioned, no new infra if Postgres is already in use) or a managed service (Pinecone/Weaviate) with an HNSW/IVF index to avoid the O(n) brute-force search.*

---

### D18 · Embeddings: TF-IDF (mock) vs. OpenAI (real) — no un modelo local descargado

- **Qué hice**: `_MockEmbedder` usa `TfidfVectorizer` de scikit-learn (ya una dependencia) para MOCK_LLM=1; `_OpenAIEmbedder` usa `text-embedding-3-small` cuando hay una API key real. Ningún modelo de embeddings local (ej. sentence-transformers) se descarga ni se ejecuta.
- *What I did: `_MockEmbedder` uses scikit-learn's `TfidfVectorizer` (already a dependency) for MOCK_LLM=1; `_OpenAIEmbedder` uses `text-embedding-3-small` when a real API key is present. No local embedding model (e.g. sentence-transformers) is downloaded or run.*

- **Por qué**: El proyecto entero corre con `MOCK_LLM=1` sin costes ni llamadas de red — un modelo local de sentence-transformers rompería eso (descarga de ~90MB, tiempo de carga, una dependencia pesada nueva) solo para la ruta mock. TF-IDF es determinístico, instantáneo, y ya está disponible vía scikit-learn.
- *Why: The whole project runs with `MOCK_LLM=1` at zero cost and no network calls — a local sentence-transformers model would break that (a ~90MB download, load time, a new heavy dependency) just for the mock path. TF-IDF is deterministic, instant, and already available via scikit-learn.*

- **Qué descarté**: sentence-transformers local para el modo mock — descartado por el motivo anterior. Embeddings hasheados sin TF-IDF (bag-of-words puro) — TF-IDF da mejores resultados con casi el mismo coste.
- *What I discarded: local sentence-transformers for mock mode — discarded for the reason above. Hashed embeddings without TF-IDF (pure bag-of-words) — TF-IDF gives better results at nearly the same cost.*

- **Qué supuse**: Que TF-IDF (similitud léxica, no semántica) es una aproximación aceptable para demostrar el mecanismo de retrieval en modo mock, aunque no capture sinónimos o paráfrasis como lo haría un embedding semántico real. Esto es una limitación documentada, no un intento de simular calidad semántica real.
- *What I assumed: That TF-IDF (lexical, not semantic, similarity) is an acceptable stand-in to demonstrate the retrieval mechanism in mock mode, even though it won't catch synonyms or paraphrasing the way a real semantic embedding would. This is a documented limitation, not an attempt to fake real semantic quality.*

---

### D19 · Cache del case store por modo (mock/real), no un único global

- **Qué hice**: `get_case_store(mock: bool)` cachea el store construido en un diccionario `dict[bool, ...]`, indexado por modo, en vez de un único objeto global.
- *What I did: `get_case_store(mock: bool)` caches the built store in a `dict[bool, ...]`, indexed by mode, instead of a single global object.*

- **Por qué**: Los tests ejercitan ambos modos (mock y real) en el mismo proceso de pytest (`test_agent_adapter.py` fuerza `MOCK_LLM=0` con `monkeypatch` para un test específico). Un único cache global habría devuelto el embedder equivocado — construido para el modo anterior — al cambiar de modo dentro del mismo proceso.
- *Why: Tests exercise both modes (mock and real) in the same pytest process (`test_agent_adapter.py` forces `MOCK_LLM=0` via `monkeypatch` for one specific test). A single global cache would have returned the wrong embedder — built for the previous mode — when switching modes within the same process.*

- **Qué supuse**: Que construir el store es lo suficientemente barato (TF-IDF sobre 42 textos cortos) para que cachear por modo, en vez de invalidar/reconstruir explícitamente, sea aceptable incluso si ambos modos se usan en el mismo proceso de producción (lo cual no debería pasar — MOCK_LLM se fija por proceso/despliegue).
- *What I assumed: That building the store is cheap enough (TF-IDF over 42 short texts) that caching per mode, rather than explicit invalidation/rebuilding, is fine even if both modes were ever used in the same production process (which shouldn't happen — MOCK_LLM is fixed per process/deployment).*

---

### D20 · Gestión de context window en retrieval: dedup + presupuesto de caracteres

- **Qué hice**: `retrieve_similar_cases` ahora sobre-recupera `2k` candidatos, elimina casos con `resolution_notes` idéntico (`_dedupe_by_resolution`), y recorta el resultado final a un presupuesto total de caracteres (`_fit_to_budget`, default 800), truncando con "…" y descartando los casos peor rankeados si el presupuesto se agota.
- *What I did: `retrieve_similar_cases` now over-fetches `2k` candidates, drops cases with identical `resolution_notes` (`_dedupe_by_resolution`), and trims the final result to a total character budget (`_fit_to_budget`, default 800), truncating with "…" and dropping the lowest-ranked cases once the budget runs out.*

- **Por qué**: Sin esto, casos casi-duplicados (mismo incidente logueado dos veces, o dos casos resueltos igual) ocupan espacio de contexto sin aportar señal nueva, y no había ningún límite explícito a cuánto texto se inyecta en el prompt — un corpus futuro con notas más largas podría acercarse al límite de tokens del modelo sin ningún control.
- *Why: Without this, near-duplicate cases (the same incident logged twice, or two cases resolved the same way) take up context space without adding new signal, and there was no explicit cap on how much text gets injected into the prompt — a future corpus with longer notes could approach the model's token limit with no control in place.*

- **Qué descarté**: Un presupuesto en tokens reales (via `tiktoken`) en vez de caracteres — más preciso, pero es una dependencia nueva para un corpus de texto corto y en un idioma consistente donde caracteres es una aproximación razonable. Deduplicación semántica (embeddings similares, no solo texto idéntico) — más robusta pero más cara de calcular; el corpus actual no tiene casos semánticamente duplicados con texto distinto, así que no se justificaba todavía.
- *What I discarded: A real token budget (via `tiktoken`) instead of characters — more precise, but a new dependency for a short-text, single-language-family corpus where characters are a reasonable approximation. Semantic deduplication (similar embeddings, not just identical text) — more robust but more expensive to compute; the current corpus has no semantically-duplicate cases with different text, so it wasn't justified yet.*

- **Qué supuse**: Que sobre-recuperar `2k` es suficiente margen para que, tras deduplicar, sigan quedando `k` casos distintos en la mayoría de queries. Con un corpus mucho más denso en duplicados, este margen tendría que crecer o el dedup tendría que aplicarse a nivel de todo el corpus antes de rankear, no solo sobre el top-`2k`.
- *What I assumed: That over-fetching `2k` is enough margin that, after deduping, `k` distinct cases remain for most queries. With a much more duplicate-dense corpus, this margin would need to grow, or dedup would need to run over the whole corpus before ranking, not just over the top-`2k`.*

---

### D21 · Golden-set eval harness — una porción real de D10

- **Qué hice**: Construí `scripts/evaluate_classifier.py` + `data/golden_set.json` (28 ejemplos etiquetados, las 6 categorías, 3 idiomas, 3 casos de prompt injection). El script corre el golden set contra el agente y reporta accuracy general y por categoría, recall de `churn_threat`, tasa de detección de prompt injection, precisión@k de retrieval (si los casos históricos recuperados comparten la categoría esperada), tasa de cumplimiento del `expected_min_urgency` (piso, no valor exacto — subestimar severidad es el fallo que importa, no sobreestimarla), y accuracy de la flag `requires_human_escalation`.
- *What I did: Built `scripts/evaluate_classifier.py` + `data/golden_set.json` (28 labeled examples, all 6 categories, 3 languages, 3 prompt-injection cases). The script runs the golden set against the agent and reports overall/per-category accuracy, `churn_threat` recall, prompt-injection detection rate, retrieval precision@k (whether retrieved historical cases share the expected category), the `expected_min_urgency` floor-compliance rate (a floor, not an exact target — underestimating severity is the failure mode that matters, not overestimating it), and `requires_human_escalation` flag accuracy.*

- **Por qué**: D10 (arriba) describe una estrategia de evaluación completa pero nunca ejecutada — "qué haría", no "qué hice". Con 28 ejemplos (no 300) y el `_MockAgent` (no un LLM real), no reemplaza ese plan, pero da un artefacto real y corrible que demuestra el mecanismo de evaluación, en vez de dejarlo solo en prosa. Los campos `expected_min_urgency`/`expected_requires_escalation` ya estaban en `golden_set.json` desde el principio pero no se usaban en `evaluate()` — encontrado en una revisión posterior y corregido, para que los datos que el golden set declara validar sean realmente los que se validan.
- *Why: D10 (above) describes a complete evaluation strategy that was never actually run — "what I would do," not "what I did." With 28 examples (not 300) and `_MockAgent` (not a real LLM), it doesn't replace that plan, but it gives a real, runnable artifact demonstrating the evaluation mechanism, instead of leaving it only as prose. The `expected_min_urgency`/`expected_requires_escalation` fields were already in `golden_set.json` from the start but weren't used in `evaluate()` — found in a later review and fixed, so what the golden set claims to validate is actually what gets validated.*

- **Resultado honesto de correrlo**: contra `_MockAgent`, 32% accuracy general — 0% en 4/6 categorías porque el stub es reglas simples (detecta prompt injection y menciones de "cancelar/churn", todo lo demás cae en `other`), no un clasificador real. 100% de detección de prompt injection. **89% de precisión@k en retrieval** — esta cifra es la que importa: confirma que el retrieval (TF-IDF, D18) funciona razonablemente bien de forma independiente a la limitación conocida del mock. Piso de urgencia cumplido solo 46% de las veces y accuracy de escalación 79% — ambos consistentes con un stub que no razona sobre severidad real, solo pattern-matching de keywords. Correr `--real` con una `OPENAI_API_KEY` real daría los números reales del clasificador, que no se han medido (ver P3 en `SELF_REVIEW.md`).
- *Honest result from running it: against `_MockAgent`, 32% overall accuracy — 0% on 4/6 categories because the stub is simple rules (detects prompt injection and "cancel/churn" mentions, everything else falls into `other`), not a real classifier. 100% prompt-injection detection. **89% retrieval precision@k** — this is the number that matters: it confirms retrieval (TF-IDF, D18) works reasonably well independent of the mock's known limitation. Urgency floor met only 46% of the time and escalation accuracy 79% — both consistent with a stub that doesn't reason about real severity, just keyword pattern-matching. Running `--real` with a real `OPENAI_API_KEY` would give the classifier's actual numbers, which haven't been measured (see `SELF_REVIEW.md` P3).*

- **Qué descarté**: Un golden set de 300 ejemplos como en D10 — no justificable para un proyecto de portfolio sin un equipo de customer service etiquetando datos reales. LLM-as-judge para evaluar `reasoning` — añade coste y una llamada real a OpenAI por ejemplo evaluado, fuera de scope para una primera versión del harness.
- *What I discarded: A 300-example golden set like D10 — not justifiable for a portfolio project without a customer service team labeling real data. LLM-as-judge to evaluate `reasoning` — adds cost and a real OpenAI call per evaluated example, out of scope for a first version of the harness.*

- **Qué supuse**: Que 28 ejemplos, aunque estadísticamente débiles para conclusiones fuertes por categoría, son suficientes para validar que el harness en sí funciona correctamente (formas de datos correctas, métricas calculadas correctamente) — la confiabilidad estadística vendría de escalar el golden set, no de cambiar el harness.
- *What I assumed: That 28 examples, while statistically weak for strong per-category conclusions, are enough to validate that the harness itself works correctly (correct data shapes, correctly computed metrics) — statistical reliability would come from scaling the golden set, not from changing the harness.*

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






## Parte 6 · Merchant Intelligence Copilot — capa de agentes multi-tool
*Merchant Intelligence Copilot — multi-tool agent layer*

> El clasificador de reclamaciones (Parte 4/4b) pasa a ser una especialidad más
> dentro de un sistema mayor: un orquestador que responde preguntas en lenguaje
> natural sobre merchants, llamando herramientas reales (SQL/KPIs, el modelo de
> churn) y recuperando contexto de políticas (RAG) con citas. Ver `src/copilot/`.
> *The complaint classifier (Parte 4/4b) becomes one specialty inside a larger
> system: an orchestrator that answers natural-language merchant questions by
> calling real tools (SQL/KPIs, the churn model) and retrieving policy context
> (RAG) with citations. See `src/copilot/`.*

### D22 · Extracción de retrieval_core.py — vector store compartido entre corpus

- **Qué hice**: Moví `SimpleVectorStore`, los embedders mock/real, y los helpers de dedup/presupuesto de contexto (antes solo en `src/parte4_api/retrieval.py`, D17-D20) a `src/copilot/retrieval_core.py`, generalizados para aceptar cualquier corpus (parametrizados por `text_field`/`dedupe_field` y cacheados por `(corpus_name, mock)` en vez de solo por `mock`). `retrieval.py` ahora importa de ahí y mantiene su propia lógica de caché específica del corpus de reclamaciones sin cambios.
- *What I did: Moved `SimpleVectorStore`, the mock/real embedders, and the dedup/context-budget helpers (previously only in `src/parte4_api/retrieval.py`, D17-D20) to `src/copilot/retrieval_core.py`, generalized to accept any corpus (parameterized by `text_field`/`dedupe_field`, cached per `(corpus_name, mock)` instead of just `mock`). `retrieval.py` now imports from there and keeps its own complaints-corpus-specific caching logic unchanged.*

- **Por qué**: El Grounding tool necesita un segundo corpus (`data/policy_docs.json`) con el mismo mecanismo de retrieval — over-fetch, cosine similarity, dedup, presupuesto de caracteres. Duplicar esa clase ya probada en vez de compartirla sería exactamente el tipo de reinvención innecesaria que D17-D19 argumentan evitar, solo que en la dirección opuesta (no añadir una dependencia nueva vs. no copiar código que ya funciona).
- *Why: The Grounding tool needs a second corpus (`data/policy_docs.json`) with the same retrieval mechanism — over-fetch, cosine similarity, dedup, character budget. Duplicating that already-tested class instead of sharing it would be exactly the kind of unnecessary reinvention D17-D19 argue against, just in the opposite direction (not adding an unneeded dependency vs. not copying working code).*

- **Qué descarté**: Reescribir `retrieval.py` para importar todo directamente sin capa de compatibilidad — descartado porque `tests/test_retrieval.py` importa `SimpleVectorStore`, `_dedupe_by_resolution`, `_fit_to_budget`, `retrieve_similar_cases` directamente desde `src.parte4_api.retrieval`; mantener esos nombres/firmas intactos evita tocar tests que no tienen nada que ver con el cambio. Verifiqué la suite completa (56 passed/1 skipped, sin cambios) inmediatamente después del refactor, antes de construir nada encima.
- *What I discarded: Rewriting `retrieval.py` to import everything directly with no compatibility layer — discarded because `tests/test_retrieval.py` imports `SimpleVectorStore`, `_dedupe_by_resolution`, `_fit_to_budget`, `retrieve_similar_cases` directly from `src.parte4_api.retrieval`; keeping those names/signatures intact avoided touching tests unrelated to this change. Verified the full suite (56 passed/1 skipped, unchanged) immediately after the refactor, before building anything on top of it.*

- **Qué supuse**: Que ningún código fuera de este repo importa `_MockEmbedder`/`_OpenAIEmbedder`/`build_case_store`/`get_case_store` directamente (solo usado internamente) — verificado por grep antes de renombrarlos sin guion bajo en el módulo compartido.
- *What I assumed: That no code outside this repo imports `_MockEmbedder`/`_OpenAIEmbedder`/`build_case_store`/`get_case_store` directly (only used internally) — verified by grep before renaming them without a leading underscore in the shared module.*

---

### D23 · Data Analyst tool — SQL parametrizado sobre DuckDB, no SQL generado por LLM

- **Qué hice**: `src/copilot/tools/data_analyst.py` registra el DataFrame de `load_clean()` en una conexión DuckDB y expone un conjunto **fijo** de 3 funciones (`top_merchants_by_tpv`, `churn_rate_by_segment`, `yoy_tpv_by_month`), adaptadas de Q1-Q3 en `src/parte2_sql.sql`. Solo argumentos tipados (validables con Pydantic en la capa de tool-calling) se bindean como parámetros (`?`) en templates SQL escritos a mano — nunca texto del usuario interpolado en el SQL.
- *What I did: `src/copilot/tools/data_analyst.py` registers `load_clean()`'s DataFrame on a DuckDB connection and exposes a **fixed** set of 3 functions (`top_merchants_by_tpv`, `churn_rate_by_segment`, `yoy_tpv_by_month`), adapted from Q1-Q3 in `src/parte2_sql.sql`. Only typed arguments (Pydantic-validatable at the tool-calling layer) get bound as parameters (`?`) into hand-written SQL templates — never user text interpolated into SQL.*

- **Por qué**: Dejar que un LLM genere SQL libre contra una conexión viva es una clase de riesgo real de inyección/exfiltración — una pregunta con prompt injection podría pedir un `DROP TABLE` o un scan sin límite disfrazado de pregunta de negocio. Fijar la forma de las queries de antemano y solo parametrizar argumentos elimina esa clase de riesgo por completo, sin perder la demostración de "el agente ejecuta SQL real", no solo describe datos.
- *Why: Letting an LLM generate free-form SQL against a live connection is a real injection/exfiltration risk class — a prompt-injected question could ask for a `DROP TABLE` or an unbounded scan disguised as a business question. Fixing the query shapes up front and only parameterizing arguments eliminates that risk class entirely, without losing the "the agent executes real SQL" proof point, not just describing data.*

- **Qué descarté**: Un esquema `country` en `top_merchants_by_tpv` calcado de Q1 — descartado porque el CSV real no tiene columna `country` (`data/README.md`); mantenerlo habría sido un parámetro que silenciosamente no hace nada. `yoy_tpv_by_month` con un self-join DuckDB calcado de Q3 — descartado en favor de reusar `monthly_kpis()` (pandas, ya testeado) y un merge explícito por `(year-1, month)`; DuckDB se usa donde aporta valor narrativo real (Q1/Q2), no de forma uniforme solo porque está disponible.
- *What I discarded: A `country` param in `top_merchants_by_tpv` copied from Q1 — discarded because the real CSV has no `country` column (`data/README.md`); keeping it would have been a parameter that silently did nothing. `yoy_tpv_by_month` with a DuckDB self-join copied from Q3 — discarded in favor of reusing `monthly_kpis()` (pandas, already tested) plus an explicit `(year-1, month)` merge; DuckDB is used where it adds real narrative value (Q1/Q2), not uniformly just because it's available.*

- **Qué supuse**: Que `min_merchants=20` (bajado del `>=100` de Q2) es razonable para el dataset real (~10k merchants); con el fixture pequeño (4 merchants) los tests pasan `min_merchants=1` explícitamente. Lo verificaría con el volumen real de merchants por segmento antes de fijar este default en producción.
- *What I assumed: That `min_merchants=20` (lowered from Q2's `>=100`) is reasonable for the real dataset (~10k merchants); with the small fixture (4 merchants) tests pass `min_merchants=1` explicitly. I'd verify against real per-segment merchant volume before fixing this default in production.*

---

### D24 · Risk tool — port de feature engineering, SHAP por instancia, y un hallazgo honesto

- **Qué hice**: `src/copilot/tools/risk.py:build_merchant_features()` reimplementa (no importa — el notebook no es import-safe) exactamente las celdas 5 y 7 de `src/parte3_modeling.ipynb` para construir las 20 features de un merchant en vivo. `score_merchant()` carga `outputs/model.pkl` y llama `predict_proba()` sobre esa fila cruda (el `ColumnTransformer` del pipeline ya hace imputación/escalado/encoding — no se reimplementa). `explain_drivers()` usa SHAP por instancia (celda 13 del notebook, aplicado a 1 fila en vez del test set completo) en vez de solo la importancia global de `outputs/feature_importance.csv`.
- *What I did: `src/copilot/tools/risk.py:build_merchant_features()` reimplements (doesn't import — the notebook isn't import-safe) exactly cells 5 and 7 of `src/parte3_modeling.ipynb` to build a live merchant's 20 features. `score_merchant()` loads `outputs/model.pkl` and calls `predict_proba()` on that raw row (the pipeline's `ColumnTransformer` already does imputation/scaling/encoding — not reimplemented). `explain_drivers()` uses per-instance SHAP (notebook cell 13, applied to 1 row instead of the full test set) instead of only `outputs/feature_importance.csv`'s global importance.*

- **Por qué SHAP por instancia**: `shap==0.46.0` ya es una dependencia fijada y el notebook ya construye el `TreeExplainer` exacto — no es una capacidad nueva, solo aplicada a una fila. Un usuario preguntando "¿por qué está marcado este merchant?" quiere una respuesta por instancia; la importancia global solo responde "qué importa en promedio", una respuesta bastante más débil para el caso de uso principal del Risk tool.
- *Why per-instance SHAP: `shap==0.46.0` is already a pinned dependency and the notebook already builds the exact `TreeExplainer` — not a new capability, just applied to one row. A user asking "why is this merchant flagged" wants a per-instance answer; global importance only answers "what matters on average," a materially weaker answer for the Risk tool's main use case.*

- **Hallazgo honesto al verificar contra el fixture**: el merchant diseñado como "alto riesgo" (90001 — TPV y approval rate colapsan, YoY entre -46% y -100% en jul-sep 2025, queja reciente) obtiene `churn_probability` **más baja** que un merchant sano (90003) al correr `score_merchant()` por primera vez de forma end-to-end. Verifiqué que no es un bug de feature engineering: `tpv_trend_3m_6m` sí distingue correctamente a ambos (0.23 vs 0.53, con ~0.5 como línea base de "estable" en una ventana de 3-de-6-meses). Es el ROC-AUC=0.58 (casi aleatorio, documentado en `outputs/model_card.md` y `SELF_REVIEW.md` P1) manifestándose de forma concreta en inferencia de un solo merchant en vivo — nunca antes ejercida end-to-end. No lo oculté ni ajusté el fixture para que "funcionara" — lo documenté aquí y en los tests (`tests/test_copilot_risk.py`), y cada respuesta de `score_merchant()` incluye `caveat` con este límite explícito, exactamente para que este tipo de discrepancia no llegue a un usuario sin contexto.
- *Honest finding when verifying against the fixture: the merchant designed as "high risk" (90001 — TPV and approval rate collapse, YoY between -46% and -100% in Jul-Sep 2025, a recent complaint) gets a **lower** `churn_probability` than a healthy merchant (90003) when running `score_merchant()` end-to-end for the first time. Verified this isn't a feature-engineering bug: `tpv_trend_3m_6m` does correctly distinguish both (0.23 vs 0.53, with ~0.5 as the "stable" baseline for a 3-of-6-month window). It's ROC-AUC=0.58 (near-random, documented in `outputs/model_card.md` and `SELF_REVIEW.md` P1) showing up concretely in live single-merchant inference — never exercised end-to-end before. I didn't hide it or tune the fixture to "make it work" — documented here and in the tests (`tests/test_copilot_risk.py`), and every `score_merchant()` response carries `caveat` with this limitation explicit, exactly so this kind of discrepancy doesn't reach a user without context.*

- **Qué descarté**: Cachear/batchear el feature engineering para todos los merchants a la vez — más eficiente para producción, pero `build_merchant_features()` recalcula sobre el DataFrame completo en cada llamada por simplicidad/fidelidad al notebook; aceptable a esta escala (fixture o CSV real de ~10k merchants), no a escala de un batch job diario. Bucketing de `risk_tier` por percentil real de la población en vez de umbrales fijos (0.10/0.20) — más principled pero requeriría puntuar a todos los merchants solo para tener percentiles, fuera de alcance para un tool que puntúa un merchant a la vez.
- *What I discarded: Caching/batching feature engineering across all merchants at once — more production-efficient, but `build_merchant_features()` recomputes over the full DataFrame on every call for simplicity/fidelity to the notebook; acceptable at this scale (fixture or the real ~10k-merchant CSV), not at daily-batch-job scale. Bucketing `risk_tier` by real population percentile instead of fixed thresholds (0.10/0.20) — more principled but would require scoring every merchant just to get percentiles, out of scope for a tool that scores one merchant at a time.*

- **Qué supuse**: Que `reference_date = df["reference_date"].max()` (igual que el notebook) es el punto de predicción correcto también en el copilot — no hay un concepto de "hoy" separado en los datos. Si el sistema pasara a producción real, el `reference_date` vendría de un reloj real, no del propio dataset.
- *What I assumed: That `reference_date = df["reference_date"].max()` (same as the notebook) is the correct prediction point in the copilot too — there's no separate "today" concept in the data. If this moved to real production, `reference_date` would come from a real clock, not the dataset itself.*

---

### D25 · Complaint classifier como tool — reutilización de Agno sin duplicar

- **Qué hice**: `src/copilot/tools/complaint_classifier.py:classify_complaint()` es un wrapper de 3 líneas sobre `build_agent()` de `src/parte4_api/agent.py` — no reimplementa clasificación, guardrails, ni el split mock/real.
- *What I did: `src/copilot/tools/complaint_classifier.py:classify_complaint()` is a 3-line wrapper over `build_agent()` from `src/parte4_api/agent.py` — it doesn't reimplement classification, guardrails, or the mock/real split.*

- **Por qué**: `build_agent()` ya contiene el split completo `_MockAgent`/`_RealAgentAdapter` (D7-D11), ambos leyendo `is_mock_mode()`. El copilot hereda ese comportamiento gratis en vez de necesitar su propia rama `MOCK_LLM` — es el mismo razonamiento de D22 (compartir en vez de duplicar), aplicado a un agente completo en vez de a una clase.
- *Why: `build_agent()` already contains the full `_MockAgent`/`_RealAgentAdapter` split (D7-D11), both reading `is_mock_mode()`. The copilot inherits that behavior for free instead of needing its own `MOCK_LLM` branch — the same reasoning as D22 (share, don't duplicate), applied to a whole agent instead of a class.*

- **Qué supuse**: Que el router (siguiente pieza, orquestador LangGraph) solo enruta aquí cuando la pregunta es una reclamación real pegada por el usuario, no una pregunta analítica ("¿qué merchants están en riesgo?") — este agente clasifica *una* reclamación, no responde preguntas generales. Documentado como advertencia explícita en el docstring del módulo para quien construya el router.
- *What I assumed: That the router (next piece, the LangGraph orchestrator) only routes here when the question is an actual complaint pasted by the user, not an analytical question ("which merchants are at risk?") — this agent classifies *one* complaint, it doesn't answer general questions. Documented as an explicit warning in the module docstring for whoever builds the router.*

---

### D26 · Orquestador LangGraph — cola acotada en vez de fan-out paralelo, y por qué Agno sigue dentro de cada nodo

- **Qué hice**: `src/copilot/graph.py` construye un `StateGraph` (`src/copilot/state.py:CopilotState`) con la forma `START -> route -> {nodo}* -> synthesize -> END`. `route` (`router.py`) calcula la lista completa y ordenada de tools a llamar **una sola vez** (`pending_tools`); `pick_next` — función pura, sin LLM — hace `pop` del primero en cada salto. Cada nodo de tool (`data_analyst_node`, `risk_node`, `grounding_node`, `complaint_classifier_node`, en `graph.py`) adapta la función pura correspondiente de `src/copilot/tools/*.py` a una actualización de `CopilotState`; las tools en sí no importan nada de LangGraph/schemas — solo `graph.py` conoce el estado del grafo.
- *What I did: `src/copilot/graph.py` builds a `StateGraph` (`src/copilot/state.py:CopilotState`) shaped `START -> route -> {node}* -> synthesize -> END`. `route` (`router.py`) computes the full ordered tool list **once** (`pending_tools`); `pick_next` — a pure function, no LLM — pops the front on every hop. Each tool node (`data_analyst_node`, `risk_node`, `grounding_node`, `complaint_classifier_node`, in `graph.py`) adapts the corresponding pure function in `src/copilot/tools/*.py` into a `CopilotState` update — the tools themselves import nothing from LangGraph/schemas, only `graph.py` knows about graph state.*

- **Por qué una cola acotada y no `Send`/fan-out paralelo de LangGraph**: Con la cola, cada tool corre secuencialmente y cada salto es una función Python pura (`pick_next`) — no hay reducers de merge concurrente que razonar, ni orden no determinista entre ramas paralelas. Esto acota las llamadas reales a LLM por request a un máximo fijo (route + synthesize + la propia llamada Agno de `complaint_classifier` si está en la ruta), sin importar cuántas tools se disparen. Un fan-out paralelo sería más rápido en wall-clock con 3+ tools, pero ese beneficio no compensa la complejidad añadida a esta escala (tools ya son rápidas — SQL en DuckDB sobre datos en memoria, un forward pass de LightGBM, retrieval TF-IDF sobre ~15-42 registros).
- *Why a bounded queue instead of LangGraph's `Send`/parallel fan-out: With the queue, each tool runs sequentially and every hop is a pure Python function (`pick_next`) — no concurrent-merge reducers to reason about, no nondeterministic ordering between parallel branches. This bounds real LLM calls per request to a fixed max (route + synthesize + complaint_classifier's own Agno call if it's in the route), regardless of how many tools fire. A parallel fan-out would be faster wall-clock with 3+ tools, but that benefit doesn't justify the added complexity at this scale (tools are already fast — DuckDB SQL over in-memory data, one LightGBM forward pass, TF-IDF retrieval over ~15-42 records).*

- **Por qué Agno sigue dentro de los nodos (no LangChain/langchain-openai también)**: LangGraph controla el grafo/estado/flujo de control; las llamadas a LLM dentro de cualquier nodo (`route_real`, `synthesize_real`, y el propio `complaint_classifier` vía `build_agent()`) siguen usando Agno. Añadir `langchain-openai` para tener un segundo patrón de "llamar a un LLM" en el mismo repo sería una dependencia nueva sin una razón real — Agno ya resuelve structured output (`output_schema`, D9) igual de bien para el router y el synthesizer que para el clasificador de reclamaciones.
- *Why Agno stays inside the nodes (not also LangChain/langchain-openai): LangGraph owns the graph/state/control-flow; LLM calls inside any node (`route_real`, `synthesize_real`, and `complaint_classifier` itself via `build_agent()`) still go through Agno. Adding `langchain-openai` for a second "call an LLM" pattern in the same repo would be a new dependency without a real reason — Agno already solves structured output (`output_schema`, D9) equally well for the router and the synthesizer as it does for the complaint classifier.*

- **Por qué `risk` también dispara `data_analyst` cuando hay `merchant_id`**: Verificado manualmente (ver D24): el modelo de churn tiene discriminación débil, así que una pregunta sobre un merchant específico se responde mejor con la señal ML (con su caveat) **junto a** evidencia KPI concreta (`yoy_tpv_by_month`), no con el score solo. Probado end-to-end contra el merchant 90001 del fixture: la respuesta combina "TPV cayó -100% YoY" (dato concreto, inequívoco) con "riesgo bajo (0%)" del modelo (señal débil, con caveat) — mostrar ambas señales sin ocultar el desacuerdo es más honesto y más útil que forzar una sola narrativa.
- *Why `risk` also triggers `data_analyst` when `merchant_id` is known: Verified manually (see D24): the churn model has weak discrimination, so a question about a specific merchant is better answered with the ML signal (with its caveat) **alongside** concrete KPI evidence (`yoy_tpv_by_month`), not the score alone. Tested end-to-end against fixture merchant 90001: the answer combines "TPV fell -100% YoY" (concrete, unambiguous fact) with "low risk (0%)" from the model (weak signal, with caveat) — showing both signals without hiding the disagreement is more honest and more useful than forcing a single narrative.*

- **Qué descarté**: Un checkpointer de LangGraph (sqlite/postgres) para persistencia — `/ask` es Q&A de un solo turno en esta versión, sin memoria entre requests, así que la capa de persistencia sería complejidad sin uso. Meter el DataFrame de transacciones dentro de `CopilotState` para evitar recargarlo por nodo — descartado porque `get_clean_transactions()` ya cachea por ruta resuelta (D-anterior en `data_analyst.py`) y `CopilotState` debe poder serializarse razonablemente, no cargar un DataFrame de pandas.
- *What I discarded: A LangGraph checkpointer (sqlite/postgres) for persistence — `/ask` is single-turn Q&A in this version, no memory across requests, so a persistence layer would be complexity with no use. Putting the transactions DataFrame inside `CopilotState` to avoid reloading it per node — discarded because `get_clean_transactions()` already caches by resolved path (earlier decision in `data_analyst.py`) and `CopilotState` should stay reasonably serializable, not carry a pandas DataFrame.*

- **Qué supuse**: Que `langgraph`'s dependencia transitiva de `langsmith` no hace ninguna llamada de red mientras `LANGCHAIN_TRACING_V2`/`LANGCHAIN_API_KEY` no estén configuradas — no las configuro en ningún sitio de este repo. Esto es parte del mismo compromiso de costo cero que el resto del proyecto (`MOCK_LLM=1`), pero es una forma no obvia de que se rompiera silenciosamente vía el comportamiento por defecto de una dependencia transitiva, así que lo documento aquí explícitamente en vez de asumir que es obvio.
- *What I assumed: That `langgraph`'s transitive `langsmith` dependency makes zero network calls as long as `LANGCHAIN_TRACING_V2`/`LANGCHAIN_API_KEY` are unset — I don't set them anywhere in this repo. This is part of the same zero-cost commitment as the rest of the project (`MOCK_LLM=1`), but it's a non-obvious way it could silently break via a transitive dependency's default behavior, so documenting it explicitly here rather than assuming it's obvious.*

---

### D27 · `/ask` como app FastAPI independiente, no montada sobre `/classify`

- **Qué hice**: `src/copilot/api.py` es una segunda app FastAPI (`uvicorn src.copilot.api:app`, puerto sugerido 8001), separada de `src/parte4_api/main.py` (`/classify`, puerto 8000) — no `app.mount(...)` de una dentro de la otra.
- *What I did: `src/copilot/api.py` is a second FastAPI app (`uvicorn src.copilot.api:app`, suggested port 8001), separate from `src/parte4_api/main.py` (`/classify`, port 8000) — not one `app.mount(...)`-ed inside the other.*

- **Por qué**: El copilot ya reutiliza `build_agent()` **en proceso** dentro de `complaint_classifier_node` (D25) — no llama a `/classify` por HTTP. Montar ambas apps juntas acoplaría sus ciclos de vida y mecanismos de `dependency_overrides` en los tests sin ningún beneficio funcional real hoy. Mantenerlas separadas también dice algo honesto: `/classify` sigue siendo un servicio real e independiente, no una fachada del copilot.
- *Why: The copilot already reuses `build_agent()` **in-process** inside `complaint_classifier_node` (D25) — it doesn't call `/classify` over HTTP. Mounting both apps together would couple their lifecycles and test `dependency_overrides` mechanics with no real functional benefit today. Keeping them separate is also an honest signal: `/classify` remains a real, independent service, not a copilot facade.*

- **Hallazgo al probarlo en vivo**: el primer request a `/ask` contra el CSV real (~200k filas, no versionado) tardó ~28s — coste de arranque en frío de `load_clean()` la primera vez que se llama en el proceso, no un problema recurrente: el segundo request tardó 65ms gracias al cache de `get_clean_transactions()` (`data_analyst.py`). Documentado aquí para que no se lea como un bug de latencia si alguien lo prueba en vivo.
- *Finding from testing it live: the first request to `/ask` against the real CSV (~200k rows, unversioned) took ~28s — a cold-start cost from `load_clean()` running for the first time in the process, not a recurring problem: the second request took 65ms thanks to `get_clean_transactions()`'s cache (`data_analyst.py`). Documented here so it doesn't read as a latency bug if someone tries it live.*

- **Qué descarté**: Reusar el `HealthResponse` de `src/parte4_api/schemas.py` en vez de definir uno nuevo — el contrato (`status`/`model`/`version`) es idéntico, así que reimplementarlo hubiera sido la misma duplicación innecesaria que D22/D25 evitan.
- *What I discarded: Defining a new HealthResponse instead of reusing `src/parte4_api/schemas.py`'s — the contract (`status`/`model`/`version`) is identical, so reimplementing it would have been the same unnecessary duplication D22/D25 avoid.*

- **Qué supuse**: Que el caller de `/ask` prefiere ver los tools que **realmente** se ejecutaron (`route`, deduplicado en orden de primera aparición) en vez de la lista cruda de `tool_calls` (que puede repetir una tool — `data_analyst` registra una entrada por cada sub-query SQL que corre).
- *What I assumed: That an `/ask` caller wants to see which tools **actually** ran (`route`, deduplicated in first-occurrence order) rather than the raw `tool_calls` list (which can repeat a tool — `data_analyst` logs one entry per underlying SQL sub-query it runs).*

---




## Decisiones extra  
*Additional decisions*

### D13 · Bump de pandas 2.2.2 a 2.2.3

- **Qué hice**: Actualicé `pyproject.toml` de `pandas==2.2.2` a `pandas==2.2.3`.
- *What I did: Updated `pyproject.toml` from `pandas==2.2.2` to `pandas==2.2.3`.*

- **Por qué**: pandas 2.2.2 no tiene wheel precompilado para Python 3.13. `uv sync` intentaba compilar desde source y fallaba en el paso de Meson/Cython. pandas 2.2.3 (patch release, misma API) sí tiene wheel para Python 3.13, y en 3.11/3.12 no hay diferencia de comportamiento.
- *Why: pandas 2.2.2 has no precompiled wheel for Python 3.13. `uv sync` tried to compile from source and failed at the Meson/Cython step. pandas 2.2.3 (patch release, identical API) does have a 3.13 wheel, and there's no behavior difference on 3.11/3.12.*

- **Qué supuse**: Que quien corra esto en 3.11 o 3.12 no ve ningún cambio de comportamiento por el bump de patch version.
- *What I assumed: That anyone running this on 3.11 or 3.12 sees no behavior change from the patch-version bump.*
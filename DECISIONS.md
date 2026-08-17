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

### D28 · Golden-set eval del copilot — construido a partir de comportamiento verificado, no de expectativas aspiracionales

- **Qué hice**: `scripts/evaluate_copilot.py` + `data/golden_set_copilot.json` (11 preguntas, incluyendo la pregunta insignia del brief original). Antes de escribir un solo `expected_route`/`expected_citation_ids`, corrí cada pregunta candidata contra el grafo real y anoté lo que de verdad devolvía — el golden set documenta comportamiento verificado, no lo que yo esperaba que pasara. Métricas: route exact-match + recall, **tasa de alucinación de citas** (todo id citado debe existir en `data/policy_docs.json` — debe ser 0%), recall de citas esperadas, tasa de mención del caveat del modelo (chequeo mecánico de la preocupación de honestidad de D24), y accuracy de clasificación para el ejemplo enrutado a `complaint_classifier`.
- *What I did: `scripts/evaluate_copilot.py` + `data/golden_set_copilot.json` (11 questions, including the flagship question from the original brief). Before writing a single `expected_route`/`expected_citation_ids`, I ran every candidate question against the real graph and recorded what it actually returned — the golden set documents verified behavior, not what I expected to happen. Metrics: route exact-match + recall, **citation hallucination rate** (every cited id must exist in `data/policy_docs.json` — should be 0%), expected-citation recall, risk-caveat mention rate (a mechanical check of D24's honesty concern), and classification accuracy for the one `complaint_classifier`-routed example.*

- **Dos bugs reales encontrados exactamente por este proceso** (documentados con su propio detalle en el commit y en `router.py`/`synthesis.py`): (1) "what is the churn rate by segment?" enrutaba solo a `risk` — la palabra "churn" disparaba el patrón de riesgo sin que nada disparase `data_analyst`, aunque `churn_rate_by_segment()` responde la pregunta directamente. (2) La frase de declive YoY en la síntesis nunca nombraba a qué merchant pertenecía, lo que la volvía ambigua en cualquier respuesta que también mencionara "top merchant by TPV" de otro merchant. Ninguno de los dos se habría encontrado sin verificar manualmente el comportamiento real antes de fijar las expectativas del golden set — la construcción del golden set fue, en sí misma, una forma de testing exploratorio.
- *Two real bugs found by exactly this process (documented in their own detail in the commit and in `router.py`/`synthesis.py`): (1) "what is the churn rate by segment?" routed to `risk` only — the word "churn" fired the risk pattern with nothing firing `data_analyst`, even though `churn_rate_by_segment()` answers the question directly. (2) The YoY-decline sentence in synthesis never named which merchant it was about, making it ambiguous in any answer that also mentioned "top merchant by TPV" for a different merchant. Neither would have been found without manually verifying real behavior before fixing the golden set's expectations — building the golden set was itself a form of exploratory testing.*

- **Por qué siempre fuerza el fixture, nunca el CSV real**: Los `merchant_id` esperados (90001-90004) y los `expected_citation_ids` son específicos del fixture — evaluar contra el CSV real (~10k merchants con IDs distintos) no tendría sentido para este golden set en particular, a diferencia de `scripts/evaluate_classifier.py` (D21), cuyo golden set no depende de qué dataset de merchants esté cargado. Esto también es lo que hace el harness corrible en CI, que nunca tiene el CSV real (`.gitignore`d por tamaño).
- *Why it always forces the fixture, never the real CSV: The expected `merchant_id`s (90001-90004) and `expected_citation_ids` are fixture-specific — evaluating against the real CSV (~10k merchants with different ids) wouldn't make sense for this particular golden set, unlike `scripts/evaluate_classifier.py` (D21), whose golden set doesn't depend on which merchant dataset is loaded. This is also what makes the harness runnable in CI, which never has the real (gitignored-by-size) CSV.*

- **Qué descarté**: Un golden set de cientos de preguntas — no justificable sin un equipo real generando/etiquetando preguntas, mismo razonamiento que D21. LLM-as-judge para puntuar la calidad prosa de la respuesta — coste y no-determinismo fuera de alcance para esta pasada, igual que D21 lo descartó para el clasificador.
- *What I discarded: A golden set of hundreds of questions — not justifiable without a real team generating/labeling questions, same reasoning as D21. LLM-as-judge to score answer prose quality — cost and non-determinism out of scope for this pass, same as D21 discarded it for the classifier.*

- **Qué supuse**: Que un golden set de 11 preguntas, aunque estadísticamente débil, es suficiente para validar que el harness en sí funciona correctamente (formas de datos correctas, métricas bien calculadas) — la fiabilidad estadística vendría de escalar el golden set, no de cambiar el harness. Mismo supuesto que D21 hace explícito para el clasificador.
- *What I assumed: That an 11-question golden set, while statistically weak, is enough to validate that the harness itself works correctly (correct data shapes, correctly computed metrics) — statistical reliability would come from scaling the golden set, not from changing the harness. Same assumption D21 makes explicit for the classifier.*

---

### D29 · Protecciones del repo — gate de CI real, pre-commit acotado, y qué no pude automatizar

- **Qué hice**: (1) `scripts/check_eval_floors.py` — lee `outputs/eval_report.json` y `outputs/eval_report_copilot.json` y falla (exit 1) si alguna métrica cae debajo de un piso anclado al valor ya commiteado (D21/D28); `.github/workflows/ci.yml` ahora corre ambos evals y este check **como gate real**, a diferencia del paso de Lint existente (`continue-on-error: true`). (2) `.pre-commit-config.yaml` — ruff (mismo pin `0.6.9` que `pyproject.toml`) + gitleaks para secretos. (3) `.github/dependabot.yml` — ecosistemas `pip` y `github-actions`, semanal. (4) `SECURITY.md` — datos sintéticos, advertencia de `joblib.load()`, guardrail de prompt injection como best-effort no como límite duro, manejo de secretos.
- *What I did: (1) `scripts/check_eval_floors.py` — reads `outputs/eval_report.json` and `outputs/eval_report_copilot.json` and fails (exit 1) if any metric drops below a floor anchored to the already-committed value (D21/D28); `.github/workflows/ci.yml` now runs both evals and this check **as a real gate**, unlike the existing Lint step (`continue-on-error: true`). (2) `.pre-commit-config.yaml` — ruff (same `0.6.9` pin as `pyproject.toml`) + gitleaks for secrets. (3) `.github/dependabot.yml` — `pip` and `github-actions` ecosystems, weekly. (4) `SECURITY.md` — synthetic data, `joblib.load()` warning, prompt-injection guardrail as best-effort not a hard boundary, secret handling.*

- **Hallazgo real al probar el pre-commit hook**: corrí `pre-commit run --all-files` para verificarlo, y el hook de ruff con `--fix` **modificó 6 archivos fuera del alcance de este trabajo** (`src/parte3_modeling.ipynb`, `src/eda/eda.ipynb`, `notebooks/databricks_parte1_pipeline.py`, y 3 archivos de test) — la misma deuda de lint preexistente que el paso Lint de CI ya trata como advisory. Revertí esos 6 archivos inmediatamente (`git checkout --`) y acoté el hook con `files:` a solo `src/copilot/`, los scripts nuevos, y `tests/test_copilot_*.py`/`conftest.py`. Un hook de pre-commit bloqueante debe respetar el mismo límite que el resto de este trabajo — no reformatear archivos que nadie pidió tocar solo porque `--fix` estaba activo.
- *Real finding from testing the pre-commit hook: I ran `pre-commit run --all-files` to verify it, and the ruff hook with `--fix` **modified 6 files outside this work's scope** (`src/parte3_modeling.ipynb`, `src/eda/eda.ipynb`, `notebooks/databricks_parte1_pipeline.py`, and 3 test files) — the same pre-existing lint debt CI's Lint step already treats as advisory. Reverted those 6 files immediately (`git checkout --`) and scoped the hook with `files:` to only `src/copilot/`, the new scripts, and `tests/test_copilot_*.py`/`conftest.py`. A blocking pre-commit hook has to respect the same boundary as the rest of this work — not reformat files nobody asked to touch just because `--fix` was on.*

- **Por qué gitleaks y no `detect-secrets`**: gitleaks es un binario estático único gestionado por el propio entorno de hooks de pre-commit — cero dependencia Python nueva en `pyproject.toml`/`uv.lock`, y sin archivo baseline que mantener (un coste de mantenimiento continuo real que `detect-secrets` sí tiene).
- *Why gitleaks and not `detect-secrets`: gitleaks is a single static binary managed entirely by pre-commit's own hook environment — zero new Python dependency in `pyproject.toml`/`uv.lock`, and no baseline file to maintain (a real ongoing-maintenance cost `detect-secrets` has).*

- **Qué NO pude hacer — protección de rama de GitHub**: este entorno no tiene `gh` CLI ni un token de GitHub configurado, así que no pude activar branch protection en `main` (requerir PR, requerir el status check `test`, prohibir force-push) vía API. Repo confirmado público, `main` como default branch, sin protección activa hoy (la API de protección devuelve "requires authentication" sin auth — la señal estándar de que no hay ninguna configurada). Queda como checklist manual para aplicar en GitHub → Settings → Branches.
- *What I could NOT do — GitHub branch protection: this environment has no `gh` CLI or GitHub token configured, so I couldn't enable branch protection on `main` (require PR, require the `test` status check, disallow force-push) via the API. Repo confirmed public, `main` is the default branch, no protection currently active (the protection API returns "requires authentication" unauthenticated — the standard signal that none is configured). This remains a manual checklist to apply in GitHub → Settings → Branches.*

- **Qué descarté**: CODEOWNERS — sin sentido real para un repo de un solo mantenedor; añadirlo sería ceremonia sin función. Hacer el paso de Lint existente bloqueante junto con el nuevo gate de eval — descartado porque arreglar la deuda de lint de los notebooks es un cleanup separado y deliberado, no un efecto secundario de añadir protecciones.
- *What I discarded: CODEOWNERS — no real purpose for a single-maintainer repo; adding it would be ceremony with no function. Making the existing Lint step blocking alongside the new eval gate — discarded because fixing the notebooks' lint debt is a separate, deliberate cleanup, not a side effect of adding protections.*

- **Qué supuse**: Que los pisos anclados a los valores ya commiteados (ej. `churn_threat_recall >= 0.5`, `citation_hallucination_rate <= 0.0`) son estables porque ambos harnesses son 100% determinísticos bajo `MOCK_LLM=1` — cualquier caída bajo el piso actual señala un cambio de código real, no ruido de ejecución a ejecución. Si en el futuro `--real` se volviera parte del gate de CI (requeriría `OPENAI_API_KEY` como secreto de GitHub Actions, fuera de alcance aquí), estos pisos tendrían que revisarse para tolerar la varianza de un LLM real.
- *What I assumed: That the floors anchored to already-committed values (e.g. `churn_threat_recall >= 0.5`, `citation_hallucination_rate <= 0.0`) are stable because both harnesses are 100% deterministic under `MOCK_LLM=1` — any drop below the current floor signals a real code change, not run-to-run noise. If `--real` ever became part of the CI gate (would need `OPENAI_API_KEY` as a GitHub Actions secret, out of scope here), these floors would need revisiting to tolerate real-LLM variance.*

---

### D30 · README reescrito alrededor del Copilot — qué se movió, qué no

- **Qué hice**: `README.md` ahora abre con el Merchant Intelligence Copilot (pitch, quickstart, ejemplo de `/ask`, tabla de arquitectura), no con "pipeline de datos y risk-scoring". El pipeline original (Partes 1-5) se movió a una sección "Appendix" al final — sigue completo, con todos los comandos que ya funcionaban, no recortado ni resumido en exceso. `src/copilot/README.md` nuevo, con el mismo nivel de detalle que `src/parte4_api/README.md` (arquitectura, endpoints, tests, limitaciones conocidas) — no un README más corto o menos cuidado solo por ser la pieza nueva.
- *What I did: `README.md` now opens with the Merchant Intelligence Copilot (pitch, quickstart, `/ask` example, architecture table), not "data pipeline and risk-scoring project". The original pipeline (Parts 1-5) moved to an "Appendix" section at the end — still complete, every command that already worked still there, not trimmed or over-summarized. New `src/copilot/README.md`, matching `src/parte4_api/README.md`'s level of detail (architecture, endpoints, tests, known limitations) — not a shorter or less-careful README just because it's the new piece.*

- **Por qué no borrar/reescribir la narrativa de Partes 1-5 en vez de mover a un apéndice**: Es trabajo real, testeado (73 tests con `--extra pyspark`), y documentado en detalle en `DECISIONS.md`/`ASSUMPTIONS.md`/`SELF_REVIEW.md` con referencias a números de línea y decisiones específicas — reescribirlo habría roto esas referencias y descartado documentación honesta sin ninguna razón funcional. El copilot es una extensión real sobre este trabajo, no un reemplazo, así que el README debía reflejar eso literalmente: el pipeline sigue siendo la base, solo ya no es lo primero que ve un lector.
- *Why not delete/rewrite the Parts 1-5 narrative instead of moving it to an appendix: It's real, tested work (73 tests with `--extra pyspark`), documented in detail in `DECISIONS.md`/`ASSUMPTIONS.md`/`SELF_REVIEW.md` with references to specific line numbers and decisions — rewriting it would have broken those references and discarded honest documentation for no functional reason. The copilot is a real extension on top of this work, not a replacement, so the README needed to literally reflect that: the pipeline is still the foundation, it's just no longer the first thing a reader sees.*

- **Qué actualicé en `TOOLS_USED.md` y por qué**: Añadí una segunda fila a la tabla de LLMs (no reescribí la original) — la fila original documenta honestamente ~20% de asistencia de IA en las Partes 1-5; esta sesión construyó la práctica totalidad de `src/copilot/` de forma agéntica, dirigida por mí en cada milestone. Dejar la cifra original sin cambios habría subestimado materialmente cuánto de este repo tiene asistencia de IA, lo cual entra en conflicto directo con el propio principio de honestidad que el archivo existe para cumplir.
- *What I updated in `TOOLS_USED.md` and why: Added a second row to the LLMs table (didn't rewrite the original) — the original row honestly documents ~20% AI assistance on Parts 1-5; this session built nearly all of `src/copilot/` agentically, directed by me at each milestone. Leaving the original figure unchanged would have materially understated how much of this repo has AI assistance, which directly conflicts with the file's own reason for existing.*

- **Qué descarté**: Fusionar `src/parte4_api/main.py` y `src/copilot/api.py` en un único README de API — descartado por la misma razón que D27 mantiene las dos apps FastAPI separadas: son dos servicios reales e independientes, documentarlos como uno solo sería inexacto.
- *What I discarded: Merging `src/parte4_api/main.py` and `src/copilot/api.py` into a single API README — discarded for the same reason D27 keeps the two FastAPI apps separate: they're two real, independent services, documenting them as one would be inaccurate.*

- **Qué supuse**: Que un lector del README (reclutador, entrevistador técnico) decide en los primeros 10-15 segundos si el proyecto es interesante — de ahí que el pitch del Copilot, no el pipeline subyacente, tenga que ser lo primero que se lee, aunque el pipeline siga siendo real trabajo documentado en detalle más abajo.
- *What I assumed: That a README reader (recruiter, technical interviewer) decides within the first 10-15 seconds whether the project is interesting — hence the Copilot pitch, not the underlying pipeline, has to be the first thing read, even though the pipeline remains real work documented in detail further down.*

---

### D31 · CI colgado 2+ horas — `numpy`/`scikit-learn` sin wheel para Python 3.13, mismo problema que D13

- **Qué hice**: Bumpeé `numpy==1.26.4` → `2.1.3` y `scikit-learn==1.5.1` → `1.5.2` en `pyproject.toml`.
- *What I did: Bumped `numpy==1.26.4` → `2.1.3` and `scikit-learn==1.5.1` → `1.5.2` in `pyproject.toml`.*

- **Qué fallaba**: Al abrir el PR, el job `test` de CI se quedó colgado 2+ horas en el paso "Install dependencies" (`uv sync --extra dev`), sin llegar siquiera a Lint. El log mostraba `Building scikit-learn==1.5.1` y `Building numpy==1.26.4` — sin wheel precompilado para Python 3.13, `uv` compila desde código fuente (C/Cython/Fortran), lento o efectivamente colgado en un runner compartido. Verifiqué en PyPI: `numpy` no tiene wheel `cp313` hasta la serie `2.x` (1.26.4 es el último release de la serie 1.26, anterior al lanzamiento de Python 3.13 en oct-2024); `scikit-learn` lo tiene desde `1.5.2`. Localmente nunca se notó porque el caché de `uv` ya tenía builds previos.
- *What failed: On opening the PR, CI's `test` job hung 2+ hours on the "Install dependencies" step (`uv sync --extra dev`), never even reaching Lint. The log showed `Building scikit-learn==1.5.1` and `Building numpy==1.26.4` — with no precompiled Python 3.13 wheel, `uv` compiles from source (C/Cython/Fortran), slow or effectively hung on a shared runner. Verified on PyPI: `numpy` has no `cp313` wheel until the `2.x` series (1.26.4 is the last 1.26 release, predating Python 3.13's Oct-2024 launch); `scikit-learn` has one starting at `1.5.2`. Never noticed locally because `uv`'s cache already had prior builds.*

- **Por qué numpy 2.1.3 y no la última 2.x**: Elegí la primera versión `2.x` con wheel `cp313` (verificado por la lista real de archivos en PyPI) en vez de la más reciente, para minimizar la distancia de comportamiento respecto a 1.26.4 — no hay razón para saltar más lejos de lo necesario en una dependencia que toca todo el pipeline.
- *Why numpy 2.1.3 and not the latest 2.x: Chose the earliest `2.x` version with a `cp313` wheel (verified via PyPI's actual file listing) instead of the newest, to minimize behavioral distance from 1.26.4 — no reason to jump further than necessary on a dependency that touches the entire pipeline.*

- **Verificación antes de commitear** (el punto de mayor riesgo real: `outputs/model.pkl` fue entrenado/pickleado bajo numpy 1.26.4/sklearn 1.5.1):
  1. `joblib.load("outputs/model.pkl")` sigue cargando correctamente — sklearn emite `InconsistentVersionWarning` (esperado en cualquier diferencia de versión, incluso un patch release; no es un error).
  2. `score_merchant()` sobre los 4 merchants del fixture da **exactamente los mismos** `churn_probability` que antes del bump (90001: 0.0007, 90002: 0.1349, 90003: 0.8217, 90004: 0.0289) — cero deriva numérica.
  3. Suite completa: 138 passed / 1 skipped, sin cambios.
  4. `check_eval_floors.py` sigue en verde con los mismos números.
- *Verification before committing (the real point of risk: `outputs/model.pkl` was trained/pickled under numpy 1.26.4/sklearn 1.5.1):*
  1. *`joblib.load("outputs/model.pkl")` still loads correctly — sklearn emits `InconsistentVersionWarning` (expected on any version difference, even a patch release; not an error).*
  2. *`score_merchant()` on all 4 fixture merchants gives **exactly the same** `churn_probability` as before the bump (90001: 0.0007, 90002: 0.1349, 90003: 0.8217, 90004: 0.0289) — zero numerical drift.*
  3. *Full suite: 138 passed / 1 skipped, unchanged.*
  4. *`check_eval_floors.py` still green with the same numbers.*

- **Qué descarté**: Bajar la versión de Python en CI a 3.12 para evitar el problema — descartado porque es un workaround que deja sin probar la versión de Python que el proyecto dice soportar (`requires-python` incluye 3.13, y D13 ya bumpeó pandas específicamente para esto), no una solución real. Re-entrenar/re-picklear `model.pkl` bajo las nuevas versiones para eliminar el warning por completo — descartado por ahora: el warning es inofensivo (verificado arriba) y regenerar un artefacto commiteado es un cambio más invasivo del necesario para desbloquear CI; queda como limpieza opcional futura, no parte de este fix.
- *What I discarded: Downgrading CI's Python to 3.12 to sidestep the problem — discarded because it's a workaround that leaves the Python version the project claims to support (`requires-python` includes 3.13, and D13 already bumped pandas specifically for this) untested, not a real fix. Re-training/re-pickling `model.pkl` under the new versions to eliminate the warning entirely — discarded for now: the warning is harmless (verified above) and regenerating a committed artifact is a more invasive change than unblocking CI requires; left as optional future cleanup, not part of this fix.*

- **Qué supuse**: Que los rangos de compatibilidad con numpy que declaran `lightgbm`/`xgboost`/`shap`/`pandas` (todos con cotas inferiores permisivas, ninguno excluye numpy 2.x explícitamente — verificado vía metadata de PyPI) reflejan compatibilidad real, no solo declarada. La verificación empírica (suite completa + predicciones idénticas del modelo) es la que realmente respalda esto, no la confianza en la metadata sola.
- *What I assumed: That the numpy compatibility ranges `lightgbm`/`xgboost`/`shap`/`pandas` declare (all permissive lower bounds, none explicitly excludes numpy 2.x — verified via PyPI metadata) reflect real compatibility, not just declared. The empirical verification (full suite + identical model predictions) is what actually backs this up, not trusting the metadata alone.*

---

### D32 · Dos PRs de dependabot rotas — por qué, y cómo se le enseñó a dependabot a no repetirlo

- **Qué fallaba**: Dos PRs abiertas por dependabot (D29) fallaban CI. (1) `scikit-learn` 1.5.2 → 1.7.2: reproduje localmente — `joblib.load("outputs/model.pkl")` lanza `AttributeError: Can't get attribute '_RemainderColsList' on <module 'sklearn.compose._column_transformer'>`, una clase interna de `ColumnTransformer` que cambió entre versiones. A diferencia del bump 1.5.1→1.5.2 de D31 (solo un warning, predicciones idénticas), este es un **fallo duro de deserialización** — el pickle del modelo ya no es compatible en absoluto. (2) `delta-spark` 3.2.1 → 4.3.1: falla en "Install dependencies", no en tests — `delta-spark` 4.x requiere `pyspark` 4.x, y dependabot solo bumpea un paquete a la vez, dejando una combinación irresoluble contra el `pyspark==3.5.3` fijado intencionalmente.
- *What failed: Two dependabot-opened PRs (D29) failed CI. (1) `scikit-learn` 1.5.2 → 1.7.2: reproduced locally — `joblib.load("outputs/model.pkl")` raises `AttributeError: Can't get attribute '_RemainderColsList' on <module 'sklearn.compose._column_transformer'>`, an internal `ColumnTransformer` class that changed between versions. Unlike D31's 1.5.1→1.5.2 bump (just a warning, identical predictions), this is a **hard deserialization failure** — the model's pickle is no longer compatible at all. (2) `delta-spark` 3.2.1 → 4.3.1: fails at "Install dependencies", not tests — `delta-spark` 4.x requires `pyspark` 4.x, and dependabot only bumps one package at a time, leaving an unresolvable combination against the intentionally-pinned `pyspark==3.5.3`.*

- **Qué hice**: Añadí reglas `ignore` a `.github/dependabot.yml`: `scikit-learn` ignora bumps minor/major (patches siguen permitidos — D31 ya demostró que un patch es seguro); `delta-spark` y `pyspark` ignoran bumps major. Recomendé cerrar ambas PRs sin mergear, no arreglarlas — no hay un fix de una línea para ninguna de las dos: la de sklearn necesitaría re-entrenar y re-picklear el modelo (cambia un artefacto commiteado y sus métricas asociadas), la de Spark necesitaría migrar todo `parte1_pyspark.py`/el notebook de Databricks a Spark 4.x. Ninguna es una "actualización de rutina".
- *What I did: Added `ignore` rules to `.github/dependabot.yml`: `scikit-learn` ignores minor/major bumps (patches still allowed — D31 already proved a patch is safe); `delta-spark` and `pyspark` ignore major bumps. Recommended closing both PRs unmerged, not fixing them — there's no one-line fix for either: the sklearn one would need retraining and re-pickling the model (changes a committed artifact and its associated metrics), the Spark one would need migrating all of `parte1_pyspark.py`/the Databricks notebook to Spark 4.x. Neither is a "routine update."*

- **Por qué esto no es un fallo de D29**: Dependabot hizo exactamente lo que se le pidió — detectar versiones desactualizadas y proponer bumps. El problema real es que este repo tiene una dependencia estructural que un bot no puede ver: un artefacto **commiteado y pickleado** (`outputs/model.pkl`) cuya compatibilidad de carga depende de la versión exacta de scikit-learn, y un par de paquetes (`pyspark`/`delta-spark`) que deben moverse juntos. Encodear esa restricción en `dependabot.yml` es la solución correcta — evita que se repitan PRs rotas cada semana, en vez de cerrar manualmente la misma PR una y otra vez.
- *Why this isn't a D29 failure: Dependabot did exactly what it was asked to do — detect outdated versions and propose bumps. The real issue is that this repo has a structural dependency a bot can't see: a **committed, pickled** artifact (`outputs/model.pkl`) whose load-compatibility depends on the exact scikit-learn version, and a package pair (`pyspark`/`delta-spark`) that must move together. Encoding that constraint into `dependabot.yml` is the correct fix — it prevents the same broken PR from recurring weekly, instead of manually closing the same PR over and over.*

- **Qué descarté**: Arreglar la PR de sklearn actualizando el código para tolerar ambos formatos de pickle — descartado, sería complejidad permanente para un problema que una regla de `ignore` resuelve en una línea. Silenciar el `ignore` para todos los paquetes ML (numpy/lightgbm/xgboost/shap también) — descartado por ahora: solo tengo evidencia real de ruptura para `scikit-learn`; extender la regla a paquetes sin evidencia sería precaución no justificada (mismo principio que D24/D31: verificar, no asumir).
- *What I discarded: Fixing the sklearn PR by updating code to tolerate both pickle formats — discarded, that would be permanent complexity for a problem an `ignore` rule solves in one line. Silencing `ignore` for all ML packages (numpy/lightgbm/xgboost/shap too) — discarded for now: I only have real evidence of breakage for `scikit-learn`; extending the rule to packages without evidence would be unjustified caution (same principle as D24/D31: verify, don't assume).*

- **Qué supuse**: Que la próxima vez que `model.pkl` se re-entrene deliberadamente (con una versión más nueva de scikit-learn), alguien recuerde quitar o ajustar esta regla `ignore` — no hay nada automático que lo haga. Vale la pena revisar esta nota si ese día llega.
- *What I assumed: That the next time `model.pkl` is deliberately retrained (under a newer scikit-learn), someone remembers to remove or adjust this `ignore` rule — nothing automated does it. Worth revisiting this note if that day comes.*

---

## Parte 7 · Despliegue en AWS (Terraform)
*AWS deployment (Terraform)*

> `terraform/` despliega el Copilot en ECS Fargate — puramente como artefacto
> de portfolio/entrevista, explícitamente no pensado para correr 24/7:
> aplicar bajo demanda para una demo, destruir después. Ver `terraform/README.md`
> para el runbook y el estado real ("no desplegado actualmente").
> *`terraform/` deploys the Copilot to ECS Fargate — purely as a
> portfolio/interview artifact, explicitly not meant to run 24/7: apply
> on-demand for a demo, destroy afterward. See `terraform/README.md` for
> the runbook and the real status ("not currently deployed").*

### D33 · Arquitectura AWS/Terraform, y todo lo que una revisión independiente encontró antes de que fuera real

- **Qué hice**: `terraform/` (VPC mínima de 2 subnets públicas, ALB, cluster/servicio ECS Fargate, ECR, IAM, CloudWatch) + un `Dockerfile` multi-stage para `src/copilot/api.py`. Alcance decidido explícitamente con el usuario antes de escribir código: **solo** el Copilot (no `parte4_api`), **sin** RDS/pgvector (la razón de D17-D19 — corpus pequeño — no cambió), **sin** S3 (los datos que el runtime necesita caben horneados en la imagen, ~1.5MB reales, no las "decenas de MB" que estimé al principio), **estado local** de Terraform (gitignored, no backend remoto S3+DynamoDB).
- *What I did: `terraform/` (a minimal 2-public-subnet VPC, ALB, ECS Fargate cluster/service, ECR, IAM, CloudWatch) + a multi-stage `Dockerfile` for `src/copilot/api.py`. Scope decided explicitly with the user before writing any code: **only** the Copilot (not `parte4_api`), **no** RDS/pgvector (D17-D19's reasoning — small corpus — hasn't changed), **no** S3 (the data the runtime needs fits baked into the image, ~1.5MB for real, not the "tens of MB" I first estimated), **local** Terraform state (gitignored, no S3+DynamoDB remote backend).*

- **Por qué me desvié de la nota original del roadmap privado sobre S3**: `context/CLAUDE.md` (gitignored) mencionaba S3 para datos/artefactos del modelo, escrito antes de que se descartaran RDS y el alcance se redujera a solo el Copilot. `data/transactions_sample.csv` (el CSV real de ~200k filas) está en `.gitignore` y nunca llega a un checkout limpio de todos modos — `src/copilot/tools/data_analyst.py:default_csv_path()` ya cae automáticamente al fixture pequeño commiteado cuando el CSV real no está presente, exactamente lo que pasa en un contenedor construido desde un checkout limpio. No hay necesidad funcional de S3 para una demo de un solo uso.
- *Why I deviated from the original private-roadmap note about S3: `context/CLAUDE.md` (gitignored) mentioned S3 for data/model artifacts, written before RDS was ruled out and scope narrowed to Copilot-only. `data/transactions_sample.csv` (the real ~200k-row CSV) is `.gitignore`'d and never reaches a clean checkout anyway — `src/copilot/tools/data_analyst.py:default_csv_path()` already falls back automatically to the small committed fixture when the real CSV isn't present, exactly what happens in a container built from a clean checkout. No functional need for S3 for a single-use demo.*

- **Revisión independiente antes de escribir Terraform**: dado que nunca puedo correr `apply` yo misma en este entorno (sin credenciales AWS), pedí una revisión de arquitectura dedicada antes de comprometerme al diseño. Encontró problemas reales, no cosméticos — todos incorporados al diseño final, no dejados como advertencias:
  1. Los security groups de Terraform deniegan todo el tráfico saliente por defecto en cuanto declaras cualquier regla de entrada (a diferencia de un SG creado por consola) — sin `egress` explícito en ambos SGs, el ALB no puede reenviar al task y el task no puede alcanzar ECR/CloudWatch.
  2. `assign_public_ip = true` no es el default de Terraform — omitirlo, sin NAT gateway, deja al task sin ninguna ruta a internet.
  3. La asociación de la tabla de rutas a las subnets es un recurso aparte de la tabla de rutas misma — crear la tabla sin la asociación dejaría las subnets silenciosamente en la tabla principal de la VPC sin ruta al IGW, un fallo que `terraform validate` no puede detectar (es comportamiento de enrutamiento en vivo, no un error de sintaxis).
  4. `target_type = "ip"` en el target group — el default es `"instance"`, que rompe el registro de targets en modo `awsvpc` de Fargate directamente. El error más común de ECS+Fargate+ALB en Terraform.
  5. `force_delete = true` en el repositorio ECR — sin esto, `terraform destroy` falla a mitad de camino contra un repo no vacío (siempre habrá al menos una imagen subida), descubierto solo en vivo contra una cuenta real a mitad de un teardown.
  6. El log group de CloudWatch debe declararse explícitamente con `retention_in_days` — si no, ECS lo autocrea al primer log con retención infinita, y como Terraform nunca lo creó, `terraform destroy` tampoco lo borra — una violación silenciosa y permanente del propio principio de "nada persiste entre demos" de este despliegue.
  7. Un rol de tarea (task role) casi vacío es correcto, no un atajo — confirmado que Fargate no impone un piso mínimo de permisos, y la app no hace ninguna llamada SDK de AWS en runtime bajo `MOCK_LLM=1`.
- *Independent review before writing Terraform: since I can never run `apply` myself in this environment (no AWS credentials), I asked for a dedicated architecture review before committing to the design. It found real, non-cosmetic problems — all folded into the final design, not left as caveats:*
  1. *Terraform security groups deny all outbound traffic by default as soon as you declare any ingress rule (unlike a console-created SG) — without explicit `egress` on both SGs, the ALB can't forward to the task and the task can't reach ECR/CloudWatch.*
  2. *`assign_public_ip = true` isn't the Terraform default — omitting it, with no NAT gateway, leaves the task with no internet path at all.*
  3. *The route table association to the subnets is a separate resource from the route table itself — creating the table without the association would silently leave the subnets on the VPC's main table with no route to the IGW, a failure `terraform validate` can't catch (live routing behavior, not a syntax error).*
  4. *`target_type = "ip"` on the target group — defaults to `"instance"`, which breaks Fargate `awsvpc`-mode target registration outright. The single most common ECS+Fargate+ALB Terraform mistake.*
  5. *`force_delete = true` on the ECR repository — without it, `terraform destroy` fails partway through against a non-empty repo (there will always be at least one pushed image), discovered only live against a real account mid-teardown.*
  6. *The CloudWatch log group must be declared explicitly with `retention_in_days` — otherwise ECS auto-creates it on first log write with infinite retention, and since Terraform never created it, `terraform destroy` never deletes it either — a quiet, permanent violation of this deployment's own "nothing persists between demos" premise.*
  7. *A near-empty task role is correct, not a shortcut — confirmed Fargate imposes no minimum permission floor, and the app makes zero AWS SDK calls at runtime under `MOCK_LLM=1`.*

- **Dos bugs reales encontrados verificando el Dockerfile de verdad (build + run + curl, no solo "el build no falló")**: (1) Cada comando de arranque documentado en este repo omite `--host`, y el default de la CLI de uvicorn es `127.0.0.1` — dentro de un contenedor eso significa inalcanzable desde fuera, y `docker exec <contenedor> curl localhost:8001` habría reportado éxito falsamente (mismo namespace de red). Verifiqué desde la **shell del host**, no desde dentro del contenedor. (2) LightGBM (el modelo de churn) enlaza dinámicamente `libgomp.so.1`, ausente en imágenes base slim — `/health` pasa igual (nunca toca el modelo), y el primer `/ask` enrutado a `risk` lanzaría `OSError` en tiempo de request, no de build. Verifiqué específicamente con una pregunta enrutada a `risk` (`"Is merchant 90001 at risk of churning and why?"`), no solo `/health` — devolvió 200 con una respuesta completa y correcta.
- *Two real bugs found by actually verifying the Dockerfile (build + run + curl, not just "the build didn't fail"): (1) Every documented run command in this repo omits `--host`, and uvicorn's CLI default is `127.0.0.1` — inside a container that means unreachable from outside, and `docker exec <container> curl localhost:8001` would have falsely reported success (same network namespace). Verified from the **host shell**, not from inside the container. (2) LightGBM (the churn model) dynamically links `libgomp.so.1`, absent on slim base images — `/health` passes anyway (never touches the model), and the first risk-routed `/ask` would throw `OSError` at request time, not build time. Specifically verified with a risk-routed question (`"Is merchant 90001 at risk of churning and why?"`), not just `/health` — returned 200 with a full, correct answer.*

- **Un tercer bug real, encontrado sin buscarlo, mientras diagnosticaba un fallo del Dockerfile**: `uv sync --frozen` (correcto para un build de contenedor reproducible) falló porque `uv.lock` seguía fijando `shap==0.46.0` aunque `pyproject.toml` decía `0.49.1`. Investigando más until, `pre-commit`, `pyspark` y `ruff` tenían la misma desincronización — D32 ya había arreglado esta misma clase de problema para `httpx`/`ipykernel`, pero cuatro merges de dependabot más la reintrodujeron para otros paquetes. `uv sync` normal (lo que corre CI) reconcilia la desincronización sobre la marcha sin fallar, así que nunca salió a la superficie ahí — hizo falta un `--frozen` real (este Dockerfile) para toparse con ella. Arreglado regenerando `uv.lock` por completo y añadiendo `uv lock --check` como paso de CI, verificado empíricamente: falla con exit 1 contra el lock desincronizado real que estaba commiteado en `main`, pasa con exit 0 contra el corregido.
- *A third real bug, found without looking for it, while diagnosing a Dockerfile failure: `uv sync --frozen` (correct for a reproducible container build) failed because `uv.lock` still pinned `shap==0.46.0` even though `pyproject.toml` said `0.49.1`. Digging further, `pre-commit`, `pyspark`, and `ruff` had the same desync — D32 had already fixed this exact class of problem for `httpx`/`ipykernel`, but four more dependabot merges since then reintroduced it for other packages. Plain `uv sync` (what CI runs) reconciles the desync on the fly without failing, so it never surfaced there — it took a real `--frozen` sync (this Dockerfile) to hit it. Fixed by fully regenerating `uv.lock` and adding `uv lock --check` as a CI step, verified empirically: exits 1 against the real desynced lock that was committed on `main`, exits 0 against the fixed one.*

- **Qué descarté**: VPC por defecto de la cuenta en vez de una dedicada — descartado porque el punto explícito de este ejercicio es demostrar habilidad de IaC/redes para entrevistas, así que construir networking real (aunque mínimo) es señal, no sobre-ingeniería, a diferencia de RDS. Estructura Terraform modularizada — descartado, modularizar implica reutilización multi-entorno que no aplica a un despliegue de un solo entorno; sería la misma clase de sobre-ingeniería que D17-D19 ya rechazan, solo aplicada a la estructura de Terraform en vez de al código de la aplicación. Un rol de tarea con `AmazonECSTaskExecutionRolePolicy` adjunto "por si acaso" — descartado, sobre-privilegiaría el rol de tarea sin ninguna necesidad real.
- *What I discarded: The account's default VPC instead of a dedicated one — discarded because the explicit point of this exercise is demonstrating IaC/networking skill for interviews, so building real (if minimal) networking is signal, not overengineering, unlike RDS. A modularized Terraform structure — discarded, modularizing implies multi-environment reuse that doesn't apply to a single-environment deployment; would be the same class of overengineering D17-D19 already reject, just applied to Terraform structure instead of application code. A task role with `AmazonECSTaskExecutionRolePolicy` attached "just in case" — discarded, would over-privilege the task role for no real need.*

- **Qué supuse**: Que el usuario correrá `terraform apply`/`destroy` desde su propia máquina con sus propias credenciales — confirmado explícitamente con él, dado que este entorno no tiene ninguna credencial de AWS. La verificación se detiene en `terraform validate` (incluyendo la rama `enable_openai_secret=true`) — un `apply` real es la única forma de confirmar el cableado de recursos de punta a punta, y eso queda fuera de lo que pude hacer aquí.
- *What I assumed: That the user will run `terraform apply`/`destroy` from their own machine with their own credentials — confirmed explicitly with them, given this environment has no AWS credentials at all. Verification stops at `terraform validate` (including the `enable_openai_secret=true` branch) — a real `apply` is the only way to confirm resource wiring end-to-end, and that's outside what I could do here.*

---

### D34 · Revisión de código de todo lo construido esta sesión — qué se arregló, qué se dejó como está y por qué

- **Qué hice**: Pedí una revisión exhaustiva (múltiples agentes en paralelo, cada uno con un ángulo distinto: reutilización, fragilidad, eficiencia, simplificación, cumplimiento de CLAUDE.md) sobre todo lo construido en `feature/merchant-copilot` (ya mergeado) y `feature/terraform-aws-deploy`. 5 de ~10 agentes terminaron con hallazgos reales; los otros 5 (diff línea por línea, auditoría de comportamiento eliminado, corrección de wrappers/proxies, trazador cross-file, pitfalls específicos del lenguaje) fallaron por un límite de sesión de la API antes de completar — no se reintentaron en esta pasada.
- *What I did: Requested an exhaustive review (multiple parallel agents, each a different angle: reuse, fragility, efficiency, simplification, CLAUDE.md compliance) over everything built in `feature/merchant-copilot` (already merged) and `feature/terraform-aws-deploy`. 5 of ~10 agents finished with real findings; the other 5 (line-by-line diff scan, removed-behavior audit, wrapper/proxy correctness, cross-file tracer, language-specific pitfalls) failed on an API session limit before completing — not retried this pass.*

- **Arreglado, con verificación real, no solo "compiló"**:
  1. `explain_drivers()` (risk.py) usaba `argsort` plano, no tie-break-stable, para elegir los top drivers SHAP — cambiado al mismo patrón lexsort ya usado en `retrieval_core.py`/el notebook de parte3.
  2. `check_eval_floors.py` confundía "métrica genuinamente ausente del reporte" (un bug real) con "métrica presente pero legítimamente `None`" (cuando el golden set no tiene ejemplos que la ejerciten) — distinguido, y el script tiene tests reales por primera vez (7 tests). Verificado manualmente inyectando ambos casos antes de escribir los tests.
  3. `ecs.tf` fijaba `MOCK_LLM="1"` como literal mientras `enable_openai_secret` era una variable real — demostrar modo real requería editar `ecs.tf` a mano. Añadida `var.mock_llm`, deliberadamente desacoplada de `enable_openai_secret` para que activar el secreto solo nunca empiece a gastar en OpenAI en silencio.
  4. El comentario que justifica `health_check_grace_period_seconds=60` afirmaba que pandas/sklearn/shap/lightgbm/duckdb/langgraph/agno importan al cargar el módulo — pero los imports de `graph.py` eran diferidos dentro de cada función de nodo. Movidos a nivel de módulo: ahora el coste de arranque documentado es el coste real, no una sorpresa de latencia en la primera pregunta real de un usuario tras cada reinicio de la tarea. Efecto secundario medido: la suite de tests bajó de ~45-98s a ~19s.
  5. `shap.TreeExplainer` se reconstruía en cada llamada a `score_merchant()` aunque es puramente función del modelo ya cacheado — cacheado ahora por identidad del objeto modelo.
  6. `get_graph()` (api.py) recompilaba el grafo de LangGraph completo en cada request de `/ask` aunque su estructura es 100% estática — cacheado con `lru_cache`.
  7. El Dockerfile documentaba `--platform=linux/amd64` solo en el comando de build, no en el propio Dockerfile — un `docker build` sin ese flag en una máquina arm64 produciría silenciosamente una imagen de arquitectura equivocada que pasa `push`/`apply` limpiamente y solo falla cuando Fargate intenta correr la tarea. Fijado con `FROM --platform=linux/amd64` en ambos stages.
  8. 7 recursos de Terraform (ALB, target group, cluster ECS, ambos security groups, el rol de ejecución, el secreto de OpenAI) tenían su `name`/`id` y su propio tag `Name` como dos literales independientes — ahora un solo `local` por recurso en `terraform/locals.tf`. Dejé sin tocar los tags de un solo uso (VPC, IGW, route table, ECR, el `name` del log group, la política inline de secrets) — un local para un string usado una sola vez es indirección sin beneficio DRY real.
  9. El `count` de `aws_route_table_association.public` era un `2` independiente del `count` de `aws_subnet.public` — ahora deriva de `length(aws_subnet.public)`.
- *Fixed, with real verification, not just "it compiled":*
  1. *`explain_drivers()` (risk.py) used plain `argsort`, not tie-break-stable, to pick top SHAP drivers — switched to the same lexsort pattern already used in `retrieval_core.py`/the parte3 notebook.*
  2. *`check_eval_floors.py` conflated "metric genuinely absent from the report" (a real bug) with "metric present but legitimately `None`" (when the golden set has no examples exercising it) — distinguished, and the script has real tests for the first time (7 tests). Manually verified by injecting both cases before writing the tests.*
  3. *`ecs.tf` hardcoded `MOCK_LLM="1"` as a literal while `enable_openai_secret` was a real variable — demoing real mode required hand-editing `ecs.tf`. Added `var.mock_llm`, deliberately decoupled from `enable_openai_secret` so enabling the secret alone never silently starts spending on OpenAI.*
  4. *The comment justifying `health_check_grace_period_seconds=60` claimed pandas/sklearn/shap/lightgbm/duckdb/langgraph/agno import at module load — but `graph.py`'s imports were deferred inside each node function. Moved to module level: the documented startup cost is now the real cost, not a surprise latency hit on a real user's first question after every task restart. Measured side effect: test suite runtime dropped from ~45-98s to ~19s.*
  5. *`shap.TreeExplainer` was rebuilt on every `score_merchant()` call despite being a pure function of the already-cached model — now cached by model object identity.*
  6. *`get_graph()` (api.py) recompiled the whole LangGraph on every `/ask` request despite its structure being 100% static — cached with `lru_cache`.*
  7. *The Dockerfile documented `--platform=linux/amd64` only in the build command, not the Dockerfile itself — a `docker build` without that flag on an arm64 machine would silently produce a wrong-architecture image that passes push/apply cleanly and only fails when Fargate tries to run the task. Fixed with `FROM --platform=linux/amd64` on both stages.*
  8. *7 Terraform resources (ALB, target group, ECS cluster, both security groups, the execution role, the OpenAI secret) had their `name`/`id` and their own `Name` tag as two independent literals — now one `local` per resource in `terraform/locals.tf`. Left single-use tags alone (VPC, IGW, route table, ECR, the log group's `name`, the inline secrets policy) — a local for a string used once is indirection with no real DRY benefit.*
  9. *`aws_route_table_association.public`'s `count` was a `2` independent of `aws_subnet.public`'s own count — now derives from `length(aws_subnet.public)`.*

- **Encontrado, deliberadamente NO arreglado** (ya son trade-offs conscientes documentados en otro lugar, o el arreglo real es más grande de lo que vale para esta pasada): `top_merchants_by_tpv` reimplementa la fórmula de TPV/approval_rate en SQL en vez de reusar `parte1_pandas.py` — intencional, es literalmente el punto del Data Analyst tool (D23: "el agente ejecuta SQL real"). `jupyter`/`ipykernel`/`xgboost` sin usar en la imagen — ya documentado como trade-off aceptado en D33. `build_merchant_features` recalcula sobre todo el DataFrame por cada candidato en `risk_node` — ya documentado como límite conocido en D24. El router en modo mock es por palabras clave hardcodeadas — ya divulgado como limitación conocida en `src/copilot/README.md`. `complaint_classifier.py` no cachea `build_agent()` — consistente con cómo `src/parte4_api/main.py` ya usa `build_agent()` (no es una regresión nueva, arreglarlo solo ahí crearía una inconsistencia). Repetición del boilerplate de 4 claves al final de cada nodo de `graph.py` — nit de mantenibilidad real pero de bajo riesgo, no arreglado esta pasada.
- *Found, deliberately NOT fixed (already conscious tradeoffs documented elsewhere, or the real fix is bigger than warranted for this pass): `top_merchants_by_tpv` reimplements the TPV/approval_rate formula in SQL instead of reusing `parte1_pandas.py` — intentional, it's literally the Data Analyst tool's point (D23: "the agent executes real SQL"). Unused `jupyter`/`ipykernel`/`xgboost` in the image — already documented as an accepted tradeoff in D33. `build_merchant_features` recomputing over the whole DataFrame per candidate in `risk_node` — already documented as a known limitation in D24. The mock-mode router being hardcoded keywords — already disclosed as a known limitation in `src/copilot/README.md`. `complaint_classifier.py` not caching `build_agent()` — consistent with how `src/parte4_api/main.py` already uses `build_agent()` (not a new regression, fixing it there alone would create an inconsistency). The 4-key boilerplate repeated at the end of every `graph.py` node — a real but low-risk maintainability nit, not fixed this pass.*

- **Qué supuse**: Que arreglar cada hallazgo reportado no siempre es lo correcto — algunos ya eran decisiones conscientes commiteadas con su propia justificación (D23, D24, D33), y "arreglarlos" habría significado deshacer una elección de diseño ya tomada con el usuario, no corregir un bug. Documentar por qué NO se arregla algo es tan importante como documentar por qué sí.
- *What I assumed: That fixing every reported finding isn't always correct — some were already conscious decisions committed with their own justification (D23, D24, D33), and "fixing" them would have meant undoing a design choice already made with the user, not correcting a bug. Documenting why something is NOT fixed matters as much as documenting why it is.*

---

### D35 · Reintento de los 5 ángulos de revisión que fallaron por límite de sesión — un hallazgo real que D34 no cerró del todo

- **Qué hice**: Los 5 ángulos de revisión que fallaron por límite de sesión de la API en D34 (wrapper/proxy correctness, cross-file tracer, line-by-line diff scan, removed-behavior auditor, language-pitfall specialist) se reintentaron en paralelo una vez Docker y la sesión estuvieron disponibles de nuevo. Además, antes de reintentar los ángulos, reconstruí la imagen Docker (fix #7 de D34) y la verifiqué de punta a punta: `docker build` produce una imagen `amd64` confirmada, `/health` responde 200, y un `/ask` enrutado a `risk` responde 200 con una respuesta real (confirma que `libgomp1`/LightGBM funcionan, no solo que el contenedor arrancó) — cierre pendiente de D34 que no había podido verificar por el daemon de Docker caído.
- *What I did: The 5 review angles that failed on an API session limit in D34 (wrapper/proxy correctness, cross-file tracer, line-by-line diff scan, removed-behavior auditor, language-pitfall specialist) were retried in parallel once both Docker and the session were available again. Before retrying the angles, I also rebuilt the Docker image (D34 fix #7) and verified it end-to-end: `docker build` produces a confirmed `amd64` image, `/health` returns 200, and a `risk`-routed `/ask` returns 200 with a real answer (confirms `libgomp1`/LightGBM actually work, not just that the container started) — closing a D34 verification gap left open by the Docker daemon being unreachable at the time.*

- **Qué encontré**: 2 de los 5 ángulos volvieron limpios (B: removed-behavior auditor, C: cross-file tracer — ambos re-verificaron independientemente la seguridad de los caches introducidos en D34 sin encontrar nada nuevo). Los otros 3 convergieron, de forma independiente, en el mismo hallazgo real: el fix de D34 #4 ("mover los imports de `graph.py` a nivel de módulo para que el costo de arranque se pague al inicio del proceso, no en la primera petición real") **no cerraba el problema del todo**. `import shap` y la deserialización de `outputs/model.pkl` vía `joblib.load()` (que importa `lightgbm` transitivamente) seguían diferidos una capa más adentro, dentro de `src/copilot/tools/risk.py`'s `load_model()`/`_get_explainer()` — nunca tocados por el diff de `graph.py`. Lo mismo con `sklearn.feature_extraction.text.TfidfVectorizer` dentro de `MockEmbedder.__init__` en `retrieval_core.py`. Medido en este venv: `import shap` ~0.3s, `joblib.load()` (deserialización + import transitivo de lightgbm) ~1.0s, construir el `TreeExplainer` ~0.25s — es decir, la primera pregunta real enrutada a "risk" o "grounding" después de cada reinicio de la tarea ECS seguía pagando ~1.3-1.9s de latencia sorpresa, exactamente lo que D34 #4 decía haber eliminado. Un hallazgo adicional de Angle E (wrapper/proxy correctness), más chico: `known_policy_ids()` releía `data/policy_docs.json` del disco en cada llamada mientras `retrieve_policy()` servía desde el cache permanente de `get_corpus_store()` — ambos podían desincronizarse si el archivo del corpus cambiaba sin reiniciar el proceso, lo cual afecta específicamente al chequeo de alucinación de citas de `evaluate_copilot.py`.
- *What I found: 2 of the 5 angles came back clean (B: removed-behavior auditor, C: cross-file tracer — both independently re-verified the safety of D34's caches and found nothing new). The other 3 independently converged on the same real finding: D34 fix #4 ("move graph.py's imports to module level so startup cost lands at process start, not on the first live request") **didn't fully close the gap**. `import shap` and `outputs/model.pkl` deserialization via `joblib.load()` (which transitively imports lightgbm) were still deferred one layer deeper, inside `src/copilot/tools/risk.py`'s `load_model()`/`_get_explainer()` — never touched by `graph.py`'s diff. Same with `sklearn.feature_extraction.text.TfidfVectorizer` inside `MockEmbedder.__init__` in `retrieval_core.py`. Measured in this venv: `import shap` ~0.3s, `joblib.load()` (deserialization + transitive lightgbm import) ~1.0s, building the `TreeExplainer` ~0.25s — meaning the first live question routed to "risk" or "grounding" after every ECS task restart was still paying ~1.3-1.9s of surprise latency, exactly what D34 #4 claimed to have eliminated. One smaller additional finding from Angle E (wrapper/proxy correctness): `known_policy_ids()` re-read `data/policy_docs.json` from disk on every call while `retrieve_policy()` served from `get_corpus_store()`'s permanent cache — the two could desync if the corpus file changed without a process restart, specifically affecting `evaluate_copilot.py`'s citation-hallucination check.*

- **Qué arreglé, verificado empíricamente**: Promoví `import shap`, `import joblib` (en `risk.py`) y `from sklearn.feature_extraction.text import TfidfVectorizer` (en `retrieval_core.py`) a nivel de módulo. Pero medí que eso solo por sí solo no alcanza: `import joblib` no importa `lightgbm` — eso pasa recién cuando `joblib.load()` deserializa el pickle y encuentra referencias a clases de lightgbm. Así que agregué una llamada de "warm-up" a nivel de módulo al final de `risk.py`: `_get_explainer(load_model())`, condicionada a que `DEFAULT_MODEL_PATH` exista. Verificado con `time.time()` alrededor de `from src.copilot.tools import risk`: el import ahora tarda ~1.85s (antes ~0s, todo diferido) y ambos caches (`_MODEL_CACHE`, `_EXPLAINER_CACHE`) quedan poblados al terminar el import — confirmando que el costo se movió de verdad al arranque, no que "compiló". Corrí `MOCK_LLM=1 uv run pytest -q` después de cada cambio: 145 passed / 1 skipped, sin regresiones. Para `known_policy_ids()`: agregué una propiedad `records` pública a `SimpleVectorStore` y reescribí `known_policy_ids()` para leer del store cacheado de `get_corpus_store()` en vez de releer el archivo — ahora ambas funciones ven exactamente el mismo snapshot del corpus durante toda la vida del proceso. Actualicé el docstring de `graph.py` para no sobre-afirmar qué paga su propio import.
- *What I fixed, empirically verified: Promoted `import shap`, `import joblib` (in `risk.py`) and `from sklearn.feature_extraction.text import TfidfVectorizer` (in `retrieval_core.py`) to module level. But measured that this alone isn't enough: `import joblib` doesn't import `lightgbm` — that only happens when `joblib.load()` deserializes the pickle and hits lightgbm class references. So I added a module-level warm-up call at the bottom of `risk.py`: `_get_explainer(load_model())`, gated on `DEFAULT_MODEL_PATH` existing. Verified with `time.time()` around `from src.copilot.tools import risk`: the import now takes ~1.85s (previously ~0s, everything deferred) and both caches (`_MODEL_CACHE`, `_EXPLAINER_CACHE`) are populated by the time the import finishes — confirming the cost genuinely moved to startup, not just "it compiled." Ran `MOCK_LLM=1 uv run pytest -q` after each change: 145 passed / 1 skipped, no regressions. For `known_policy_ids()`: added a public `records` property to `SimpleVectorStore` and rewrote `known_policy_ids()` to read from `get_corpus_store()`'s cached store instead of re-reading the file — both functions now see exactly the same corpus snapshot for the process's whole lifetime. Updated `graph.py`'s docstring to stop over-claiming what its own import pays for.*

- **Qué supuse**: Que "arreglado y verificado" para un problema de timing de imports significa medir el costo real con un cronómetro, no solo confirmar que el import no lanza una excepción — un `import shap` a nivel de módulo que no dispara el trabajo pesado real (deserializar el modelo, construir el explainer) es una corrección cosmética, no la que el docstring de `graph.py` prometía. También asumí que agregar una llamada de arranque incondicional en `risk.py` es seguro porque `outputs/model.pkl` está commiteado al repo (no gitignored) y todos los tests existentes ya lo usan directamente sin mockear una ruta alternativa — verificado con `grep` antes de escribir el cambio, no asumido a ciegas.
- *What I assumed: That "fixed and verified" for an import-timing problem means measuring the real cost with a stopwatch, not just confirming the import doesn't raise — a module-level `import shap` that doesn't trigger the actual heavy work (deserializing the model, building the explainer) is a cosmetic fix, not the one `graph.py`'s docstring promised. I also assumed adding an unconditional warm-up call in `risk.py` is safe because `outputs/model.pkl` is committed to the repo (not gitignored) and every existing test already uses it directly without mocking an alternate path — verified with `grep` before writing the change, not blindly assumed.*

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
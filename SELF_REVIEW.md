# SELF_REVIEW.md — Payne, Anna

> Identifica **≥ 3 problemas reales de tu propia solución**. La honestidad gana puntos.
> *Identify ≥ 3 real problems with your own solution. Honesty earns points.*

---

## P1 · ROC-AUC de 0.58, modelo con poder predictivo limitado
*ROC-AUC of 0.58, model with limited predictive power*

- **Qué falla**: El modelo de churn (de Parte 3) obtiene ROC-AUC = 0.58 y PR-AUC=0.11 en el test set. El PR-AUC base (predecir siempre la clase mayoritaria de forma proporcional) seria 0.0875. Aqui, el lift es real pero modesto.
- *What fails: The churn model (part 3) achieves ROC-AUC = 0.58 and PR-AUC = 0.11 on the test set. The baseline PR-AUC (always predicting the majority class proportionally) would be about 0.0875. Here the lift is real but modest.*

- **Por qué falla**: Con una sola snapshot de `reference_date = 2025-09-30` y solo features puramente transaccionales, la señal predictiva es débil. Los factores más potentes de churn en acquiring (cambio de proveedor, problema de pricing, interacción con customer service) no están en el dataset. Además, el churn sintético fue generado con probabilidades Gaussianas que se solapan entre los features, haciendo el problema genuinamente dificil sin leakage.

- *Why it fails: With a single snapshot of `reference_date = 2025-09-30` and only purely transactional features, the predictive signal is weak. The most powerful churn factors in acquiring (provider change, pricing issue, customer service interaction) are not in the dataset. Additionally, synthetic churn was generated with Gaussian probabilities that overlap across features, making the problem genuinely difficult without leakage.*



- **Cómo lo arreglaría con más tiempo**: 
* Solicitar datos de customer service (n_tickets, tiempo_resolución) que el EDA indicó como altamente correlacionados con churn real. 
* Agregar features de tendencia a horizonte mas largo (6m vs 12m) para capturar merchants quien son en declive lento. 
* Calibrar las probabilidades con isotonic regression para que el score sea util como probabilidad real, y no solo como ranking. 


- *How I would fix it with more time:* 
* *Request customer service data so that EDA indicated as highly correlated with real churn.*
*  *Add trend features over a longer horizon (6m vs 12m) to capture merchants who are in slow decline.* 
* *Calibrate probabilities w isotonic regression so the score is useful as a real probability and not just ranking.*



- **Impacto en producción**: Un modelo con AUC = 0.58 priorizaría mal a los merchants en riesgo. A recall@10%, solo se recuperaría ~14% de los churners. El equipo de retención gastaría llamadas en merchants que no van a churnar. Sería marginalmente mejor que un selección aleatoria, pero no por mucho.
- *Production impact: a model with AUC = 0.58 would poorly prioritize any of the at risk merchants. At recall@10%, only 14% of churners would be recovered. The retention team would spend calls on merchants that are not going to churn. It would be marginally better than simply random selection, but not by much.*



---



## P2 · `quality_report` relee el CSV desde un ruta hardcodeada
*`quality_report` rereads the CSV from hardcoded path*

- **Qué falla**: Dentro de `quality_report(df, raw_path="data/transactions_sample.csv")`, si el raw_path no existe, la funcion cae back en el df limpio, lo que produce conteos aproximados. La ruta por defecto `"data/transactions_sample.csv"` asume que el proceso se ejecuta desde la raíz

- *What fails: Inside `quality_report(df,raw_path="data/transactions_sample.csv")`, if the raw_path doesn't exist, the function falls back to the clean df which produces approximate counts. The default path `"data/transactions_sample.csv"` assumes the process runs from the root*

- **Por qué falla**: La funcion recibe el DF limpio (post `load_clean`) pero necesita el CSV crudo para contar con exactitud cuantas filas tenían formato BR en `amount` (197913). Una vez limpiados, esos valores son floats y la cuenta original se pierde. La solucion correcta sería que `quality_report` reciba el df crudo antes de limpiar, o que `load_clean` devuelva un log de transformaciones.  

- *Why it fails: The function receives the clean DF (post `load_clean`) but needs the raw CSV to count how many rows had BR format in `amount` (197913). Once cleaned, those values are float and the original count is lost. The correct solution would be for `quality_report` to receive the raw df before cleaning, or for `load_clean` to return transformations log.*


- **Cómo lo arreglaría con más tiempo**: Refactorizar `load_clean` para que devuelva también un diccionario `transforms_log` con los conteos de cada transformación aplicada. `quality_report` consumiría ese log en lugar de releer CSV

- *How I would fix it with more time: Refactor `load_clean` to also return a `transforms_log` dic with counts of each transformation applied. `quality_report` would consume that log instead of rereading tge CSV.*

- **Impacto en producción**: Si el CSV cambia de nombre/ubicación, `quality_report` devuelve conteos aproximados del df limpio sin advertir. En un pipeline de datos real, esto podría enmascarar problemas de calidad con un nuevo lote de datos.
- *Production impact: If the CSV changes name or location, `quality_report` returns approximate counts from the clean df without warning. In a real data pipeline, this could mask quality issues with new batches of data.*

---

## P3 · (Resuelto) La Parte 4 solo implementaba el mock agent, no el agente Agno real
*(Fixed) Part 4 only implemented the mock agent, not the real Agno agent*

- **Qué fallaba**: `build_agent()` construía un `Agent` de Agno real cuando `MOCK_LLM` no estaba activo, pero `main.py` llamaba `agent.classify(...)` — un método que solo `_MockAgent` implementaba. El `Agent` real de Agno solo expone `.run()`/`.arun()`, así que cualquier request sin `MOCK_LLM=1` fallaba con `AttributeError` (envuelto en un 502) antes incluso de llegar a OpenAI.
- *What failed: `build_agent()` constructed a real Agno `Agent` when `MOCK_LLM` wasn't set, but `main.py` called `agent.classify(...)` — a method only `_MockAgent` implemented. Agno's real `Agent` only exposes `.run()`/`.arun()`, so any request without `MOCK_LLM=1` failed with an `AttributeError` (wrapped as a 502) before ever reaching OpenAI.*

- **Cómo se arregló**: se añadió `_RealAgentAdapter` en `agent.py`, que envuelve el `Agent` real y expone `.classify(...)` con la misma firma que `_MockAgent`, llamando a `agent.run(...)` internamente y adaptando `RunOutput.content` al dict que `main.py` espera. También se detectó y corrigió que la versión instalada de Agno renombró `response_model` a `output_schema`, y que pasarle `ClassifyResponse` directamente forzaría al LLM a inventar un `latency_ms` (dato que debe medir el caller, no generarlo el modelo) — se creó `_LLMClassification`, el mismo schema sin `merchant_id`/`latency_ms`, para el `output_schema` del agente real.
- *How it was fixed: added `_RealAgentAdapter` in `agent.py`, wrapping the real `Agent` to expose `.classify(...)` with the same signature as `_MockAgent`, calling `agent.run(...)` internally and adapting `RunOutput.content` into the dict `main.py` expects. Also found and fixed that the installed Agno version renamed `response_model` to `output_schema`, and that passing `ClassifyResponse` directly would force the LLM to invent a `latency_ms` value (something the caller should measure, not the model generate) — created `_LLMClassification`, the same schema minus `merchant_id`/`latency_ms`, for the real agent's `output_schema`.*

- **Qué no se pudo verificar**: sin una `OPENAI_API_KEY` real no se pudo ejecutar un request end-to-end contra OpenAI. Lo verificado: el `Agent` real se construye sin error con el `output_schema` correcto, y el adaptador expone la interfaz esperada. Un test de integración real (`pytest.mark.skip(reason="requires OPENAI_API_KEY")` cuando no está disponible) sigue siendo el siguiente paso pendiente.
- *What couldn't be verified: without a real `OPENAI_API_KEY`, an end-to-end request against OpenAI couldn't be run. What was verified: the real `Agent` constructs without error with the correct `output_schema`, and the adapter exposes the expected interface. A real integration test (`pytest.mark.skip(reason="requires OPENAI_API_KEY")` when unavailable) is still the pending next step.*



---



## Problemas adicionales
*Additional problems*

### P4 · Split de train/test sin validación cruzada
*Train/test split without cross-validation*

- **Qué falla**: El modelo de Parte 3 usa único split 80/20. Con 9982 merchants y solo 873 positivs, el test set tiene 174 churners, es suficiente para una estimación de AUC pero viene con alta varianza. Una estimación con 5 fold CV estratificado daría un intervalo de confianza más honesto.
- *What fails: The Part 3 model uses one single 80/20 split. With 9982 merchants and only 873 positives, the test set has ~174 churners, which is  enough for an AUC estimate but with high variance. An estimate with a stratified 5 fold CV would give a more honest confidence interval.*

- **Cómo lo arreglaría**: `StratifiedKFold(n_splits=5)` en el loop de entrenamiento y reporte de media += std del AUC.
- *How I would fix it: `StratifiedKFold(n_splits=5)` in the training loop and report of mean += std of AUC.*

- **Impacto en producción**: La estimacion de AUC=0.58 tiene una incertidumbre no cuantificada que podría ser +=0.05       
- *Production impact: The AUC=0.58 estimate has an unquantified uncertainty that could be +=0.05.*



### P5 · `merchants_at_risk` no descarta merchants inactivos recientemente por razones legítimas
*`merchants_at_risk` doesn't exclude merchants recently inactive for legitimate reasons*

- **Qué falla**: Un merchant que cerró por vacaciones 30 días exactamente puntúa alto en TPV drop aunque no esté en riesgo de churn. La heurística no distingue entre inactividad temporal y un declive real.  
- *What fails: A merchant that is closed for holidays for exactly 30 days scores high on TPV drop, even though it is not at churn risk. The heuristic does not distinguish between temporary inactivity and a real decline*

- **Cómo lo arreglaría**: Añadir una feature de "historico de gaps de actividad" para normalizar la señal de caída de TPV por la variabilidad historica del merchant (coeficiente de variación del TPV mensual).
- *How I would fix it: Add an activity gap history feature to normalize TPV drop signal by the merchants historical variability (coefficient of variation of monthly TPV)*

---



## Lo que sí me salió bien
*What did go well!*

- La detección de las 5 trampas plantadas (T1-T5) y la trampa adicional de transacciones post-reference_date. Todas fueron identificadas en el EDA y bloqueadas en la ingeniería de features.

- El endpoint de la API arranca con `MOCK_LLM=1`, los 5 tests obligatorios pasan, y la estructura Pydantic v2 es tipada y validada correctamente.

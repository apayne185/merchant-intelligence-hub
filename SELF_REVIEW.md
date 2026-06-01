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

## P3 · La Parte 4 solo implementa el mock agent, no el agente Agno real
*Part 4 only implements the mock agent, not the real Agno agent*

- **Qué falla**: El agente real de Agno (`build_agent` con un `Agent` real de Agno con OpenAI) solo está esqueletado, porque en producción requiere `OPENAI_API_KEY` y no se puede validar sin incurrir en costes. Lo que se entrega funcional es el `_MockAgent`, que es determinístico pero no ejercita realmente el framework de Agno.
- *What fails: The real Agno agent (`build_agent` with a real Agno `Agent` with OpenAI) is only skeletonized, as in production it requires `OPENAI_API_KEY` and cannot be validatd without incurring costs. What is delivered as functional is the `_MockAgent`, which is deterministic but does not really exercise the Agno framework.*

- **Por qué falla**: Sin una API key de OpenAI disponible durante el desarrollo, no pude iterar sobre el comportamiento real del agente. El mock cubre los tests obligatorios, pero no demuestra que la integración del Agno, OpenAI, Pydantic response_model funciona en la práctica.
- *Why it fails: Without an OpenAI API key available during developmnt, I couldn't iterate on the real agent behavior. The mock covers the required tests, but does not demonstrate that the Agno, OpenAI, Pydantic response_model integration works in practice.*

- **Cómo lo arreglaría con más tiempo**: implementar el Agent de Agno real con los tools decorados, testear con `MOCK_LLM=0` y con una key de dev, y añadir un test de integracion separado que se salte con `pytest.mark.skip(reason="requires OPENAI_API_KEY")` cuando no esta disponible.

- *How I would fix it with more time: implement the real Agno Agent with decorated tools, test with `MOCK_LLM=0`development and a dev key, and add anohter separate integration test that skips with `pytest.mark.skip(reason="requires OPENAI_API_KEY")` when its not available.*

- **Impacto en producción**: El endpoint `/classify` devolvería un mnesaje de 502 sin `MOCK_LLM=1` y tambien sin una API key valida. El evaluador lo detectaría si intenta arrancar sin el mock.
- *Production impact: The `/classify` endpoint would return 502 without `MOCK_LLM=1` and also without a valid API key. The evaluator would detect this if they would try to start without the mock.*



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

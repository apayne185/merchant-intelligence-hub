# ASSUMPTIONS.md — Payne, Anna

> Reasoning behind the pipeline design: the original spec left several points ambiguous (3 intentional, plus 2 I found while building). For each assumption: (1) what the spec left ambiguous, (2) what I assumed, (3) how I'd verify it with a real stakeholder, (4) impact if the assumption turns out to be wrong.

---

## A1 · Definición de TPV incluye reversals?
*Definition of TPV include reversals?*

- **Qué dice el spec (es ambiguo)**: El enunciado pide "TPV aprobado" en Q1 SQL y "tpv" en `monthly_kpis`. No especifica si TPV incluye los transacciones `reversed` (que se aprueban, se revierten) ni si solo cuenta `approved`.    


- **Qué supuse**: TPV=suma de `amount` donde `status= 'approved'` unicamente. Las transacciones `reversed` se excluyen, porque el dinero se devuelve al dueno, por lo que no cuenta como volumen procesado neto. Las `denied` claramente quedan fuera.

- **Cómo lo verificaría con stakeholder**: Preguntar al equipo de finanzas si el TPV que aparece en los dashboards de Getnet incluye o excluye reversals. En muchos adquirentes se reporta TPV bruto (incluye reversals) y TPV neto por separado.

- **Impacto si mi supuesto es falso**: Si TPV incluye reversals (`status IN ('approved',  'reversed')`), los KPIs mensuales estarían subestimados un 2% (tasa de reversals observada en dataset). Para el ranking Q1 SQL, podría cambiar merchants con alta tasa de devoluciones (fraude). Para las features del modelo ML, la señal relativa entre merchants se preservaría aunque el valor absoluto cambie.




---

## A2 · Ventana temporal de "señal débil" en `merchants_at_risk`
*Time window for weak signal in merchants_at_risk*

- **Qué dice el spec (ambiguo)**: El enunciado pide "señal débil de pre-churn" sin especificar ventana temporal. No queda claro si "reciente" es 7 días, 30 días, 90 días, o es un estandar interno de Getnet    

- **Qué supuse**: Usé 30 dias antes de `reference_date` para las 3 señales (TPV drop, approval rate baja, queja reciente). Esta eleccion asume que el equipo de retencion trabaja con ciclos mensuales de revision.

- **Cómo lo verificaría con stakeholder**: Preguntar al equipo de Customer Success cuantos días antes de el churn observado detectan los primeros indicios. Si tienen historial de intervenciones exitosas, estimar que lead time óptimo de la señal con un análisis de supervivencia

- **Impacto si mi supuesto es falso**: Si la ventana correcta es 7 dia, la lista estaría contaminada por merchants con un simple fin de semana inactivo. Si fuera 90 dias, la señal se diluye con merchants ya en declive profundo que estan mas alla de retención. El parametro debería ser configurable en producción



---

## A3 · Granularidad del target (fla_churn90 permite reactivaciones) 
*Target granularity (does fla_churn90 allow reactivations)*

- **Qué dice el spec (ambiguo)**: `fla_churn90` aparece en la tabla de transacciones (una fila por transaccion), lo que implica que todos los registros de un merchant comparten el mismo flag. No queda claro si en produccion un merchant puede tener multiples etiquetas en distintas fechas de referencia (churn a reactivación a rechurn).

- **Qué supuse**:`fla_churn90` es una etiqueta de snapshot: si el merchant churneó en 90 días siguientes a `reference_date= 2025-09-30`, el flag es 1 para todas sus transacciones en ese snapshot. Traté el target como merchant level usando `drop_duplicates("merchant_id")`, ya que todos los registros de un merchant tienen el mismo valor.

- **Cómo lo verificaría con stakeholder**: Preguntar al equipo de datos si `churn_labels` en el warehouse tiene una fila por merchant por mes o una fila unica permanente. Comprobar si existen merchants con fla_churn90=1 en un snapshot y fla_churn90=0 en otro (reactivaciones historicas).

- **Impacto si mi supuesto es falso**: Si el target permite reactivaciones, el modelo debería incluir features del churn historico y el split debería ser rolling origin temporal en lugar de crosssectional. El problema se convierte en predicción de estado en lugar de clasificación unica    


---





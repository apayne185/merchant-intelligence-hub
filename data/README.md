# `data/` — datasets

Datos de entrada para el pipeline. Tratarlos como inmutables (read-only); los derivados van en `outputs/`.

## Archivos

### `transactions_sample.csv` (~200k filas · ~80 MB)

Transacciones simuladas de merchants. Columnas: `transaction_id`, `merchant_id`, `transaction_date`,
`amount`, `status` (`approved`/`denied`/`reversed`), `channel`, `cancellation_reason`,
`reference_date`, `fla_churn90`, `last_complaint_date`, `segment`, `mcc`, `dat_process`.

⚠️ **Datos sintéticos.** No hay PII real.

⚠️ **Contiene problemas de calidad plantados a propósito** (formatos de fecha mixtos, decimales en
formato BR, duplicados, leakage temporal — ver `quality_report` en Parte 1 y `DECISIONS.md`).

**No está versionado** (`.gitignore`, por tamaño ~80MB). Si cloneas este repo sin el CSV, la mayoría
del pipeline no tiene con qué correr — no hay un generador incluido en el repo para regenerarlo.

### `merchants_context.json` (~500 merchants)

Input para la tool `get_merchant_context` de Parte 4. Esquema:

```json
[
  {
    "merchant_id": 10063716,
    "segment": "SMB",
    "tpv_last_3m": 124500.50,
    "n_complaints_30d": 2,
    "days_since_last_complaint": 5
  },
  ...
]
```

> Si tu agente recibe un `merchant_id` que no está en el JSON, decide qué hacer y documéntalo en `DECISIONS.md`.


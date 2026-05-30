# `data/` — datasets proporcionados

Este directorio contiene los datos que necesitarás durante el test. **No los modifiques**; trátalos como inmutables (read-only).

## Archivos

### `transactions_sample.csv` (~200k filas · ~80 MB)

Transacciones simuladas de merchants en Brasil. Esquema documentado en `STATEMENT.md` (Parte 1).

⚠️ **Datos sintéticos.** No hay PII real. Generados a partir de distribuciones plausibles del negocio de Getnet.

⚠️ **Contiene problemas plantados a propósito.** No los hemos documentado para ti — encontrarlos y reportarlos forma parte del test (`quality_report` en Parte 1).

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

## Reglas

- **No publiques estos datos** (GitHub público, foros, etc.). Uso exclusivo del proceso de selección.
- **No los incluyas en tu `.zip` final** — ya los tenemos. El `.gitignore` los excluye por defecto.
- Sí incluye los **derivados** que tu código genere en `outputs/` (KPIs, reportes, predicciones).

# `data/` — datasets

Datos de entrada para el pipeline. Tratarlos como inmutables (read-only); los derivados van en `outputs/`.

## Archivos

### `transactions_sample.csv` (~200k filas · ~18 MB)

Transacciones simuladas de merchants. Columnas: `transaction_id`, `merchant_id`, `transaction_date`,
`amount`, `status` (`approved`/`denied`/`reversed`), `channel`, `cancellation_reason`,
`reference_date`, `fla_churn90`, `last_complaint_date`, `segment`, `mcc`, `dat_process`.

⚠️ **Datos sintéticos.** No hay PII real.

⚠️ **Contiene problemas de calidad plantados a propósito** (formatos de fecha mixtos, decimales en
formato BR, duplicados, leakage temporal — ver `quality_report` en Parte 1 y `DECISIONS.md`).

**No está versionado** (`.gitignore`, por tamaño ~18MB). Si cloneas este repo sin el CSV, la mayoría
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

### `copilot_fixture_transactions.csv` (222 rows, 4 merchants)

Small, **versioned** (unlike `transactions_sample.csv` above) transactions
fixture for `src/copilot/tools/data_analyst.py` and `risk.py`, and for the
copilot golden-set eval (`data/golden_set_copilot.json`). Same columns/format
as `transactions_sample.csv` (BR-format `amount`, ISO `transaction_date`) so
`src.parte1_pandas.load_clean()` parses it identically — no special-casing.
Exists because the real CSV is gitignored by size and unavailable in CI; this
is what lets the copilot's tool tests and eval harness run in CI without it.

4 merchants with deliberately distinct, verified profiles (`monthly_kpis`/
`merchants_at_risk` run against this fixture on 2026-08-10 to confirm):

| merchant_id | segment | mcc | profile | Sep-2025 signal |
|---|---|---|---|---|
| 90001 | SMB | 5812 | **High risk** — steady through Jun-2025, then TPV and approval rate collapse Jul-Sep 2025, complaint on 2025-09-20, `fla_churn90=1` | approval_rate 0%, tpv ≈ 0 → `risk_score` 1.0, `top_signal=tpv_drop` |
| 90002 | Enterprise | 5411 | **Healthy** — stable/growing TPV, high approval rate, no complaints, `fla_churn90=0` | approval_rate 100%, TPV growing → `risk_score` 0.0 |
| 90003 | SMB | 5691 | **Moderate, short history** — only Apr-Sep 2025 (no 2024 data) — deliberate edge case: no YoY comparison possible | stable, low risk |
| 90004 | Enterprise | 5812 | **Top volume** — highest TPV of the four, for "top merchants by TPV" queries | approval_rate ~83-100%, healthy |

Deterministic (`random.seed(42)`); regenerate via
`uv run python -m scripts.generate_copilot_fixture` if the merchant profiles
ever need to change.


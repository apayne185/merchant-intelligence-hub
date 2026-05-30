"""
Parte 1 · Análisis exploratorio en pandas (15 pts)
==================================================

Implementa las 4 funciones que aparecen abajo. Lee el `STATEMENT.md` antes de
empezar para entender los requisitos y la rúbrica.

Reglas:
- Código vectorizado. NO loops sobre filas.
- Type hints en las firmas públicas.
- Documenta tus decisiones en `DECISIONS.md`, no aquí.
- El CSV tiene problemas a propósito. Encontrarlos forma parte del test.

Ejecuta con:
    python -m src.parte1_pandas data/transactions_sample.csv

(o adapta el `if __name__ == "__main__"` a tu gusto)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd


# -----------------------------------------------------------------------------
# 1.1  load_clean
# -----------------------------------------------------------------------------
def load_clean(path: str | Path) -> pd.DataFrame:
    """
    Carga el CSV y devuelve un DataFrame listo para análisis.

    Documenta en `DECISIONS.md`

    Args:
        path: ruta al CSV.

    Returns:
        DataFrame limpio.
    """
    # TODO: implementa
    raise NotImplementedError("Parte 1.1 · load_clean")


# -----------------------------------------------------------------------------
# 1.2  monthly_kpis
# -----------------------------------------------------------------------------
def monthly_kpis(df: pd.DataFrame) -> pd.DataFrame:
    """
    KPIs mensuales por merchant. Una fila por (merchant_id, month).

    Columnas de salida:
      - merchant_id
      - month               (primer día del mes, dtype datetime64[ns])
      - tpv                 (suma de amount para status == 'approved')
      - approval_rate       (% transacciones aprobadas sobre total)
      - pct_ecom            (% del TPV que viene del canal 'ecom')
      - n_tx                (número de transacciones del mes)

    Vectorizado. Sin loops.

    Args:
        df: DataFrame ya limpio (output de load_clean).

    Returns:
        DataFrame con los KPIs.
    """
    # TODO: implementa
    raise NotImplementedError("Parte 1.2 · monthly_kpis")


# -----------------------------------------------------------------------------
# 1.3  quality_report
# -----------------------------------------------------------------------------
def quality_report(df: pd.DataFrame) -> dict[str, Any]:
    """
    Devuelve un reporte de calidad de datos con al menos 5 problemas detectados.

    Estructura esperada:
        {
          "issues": [
            {
              "column": "<nombre>",
              "rows_affected": <int>,
              "impact": "<descripción del impacto en análisis/modelo>",
              "fix": "<cómo lo resolverías>",
            },
            ...
          ],
          "summary": {
            "n_rows": <int>,
            "n_cols": <int>,
            "n_issues": <int>,
          }
        }
    """
    # TODO: implementa
    raise NotImplementedError("Parte 1.3 · quality_report")


# -----------------------------------------------------------------------------
# 1.4  merchants_at_risk
# -----------------------------------------------------------------------------
def merchants_at_risk(df: pd.DataFrame, top_n: int = 200) -> pd.DataFrame:
    """
    Devuelve los `top_n` merchants con mayor "señal débil" de pre-churn.

    Tú decides la heurística. Justifícala en `DECISIONS.md`.

    Columnas mínimas esperadas:
      - merchant_id
      - risk_score          (float, mayor = más riesgo)
      - top_signal          (str, qué señal dominó tu score)

    Args:
        df: DataFrame limpio.
        top_n: número de merchants a devolver.

    Returns:
        DataFrame ordenado por risk_score descendente.
    """
    # TODO: implementa
    raise NotImplementedError("Parte 1.4 · merchants_at_risk")


# -----------------------------------------------------------------------------
# Entry point — genera artefactos en outputs/
# -----------------------------------------------------------------------------
def main(csv_path: str) -> None:
    """Carga, calcula KPIs y reporte, y persiste en outputs/."""
    outputs = Path("outputs")
    outputs.mkdir(exist_ok=True)

    df = load_clean(csv_path)

    kpis = monthly_kpis(df)
    kpis.to_csv(outputs / "monthly_kpis.csv", index=False)

    report = quality_report(df)
    (outputs / "quality_report.json").write_text(json.dumps(report, indent=2, default=str))

    at_risk = merchants_at_risk(df, top_n=200)
    at_risk.to_csv(outputs / "merchants_at_risk.csv", index=False)

    print("✓ Outputs generados en outputs/")


if __name__ == "__main__":
    csv = sys.argv[1] if len(sys.argv) > 1 else "data/transactions_sample.csv"
    main(csv)

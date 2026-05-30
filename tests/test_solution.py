"""
Tests para las Partes 1-3 (pandas, ML).

Añade aquí los tests que validan tu código. No hay tests obligatorios para
estas partes, pero **tener tests propios reales suma puntos** (bonus).

Recomendación mínima:
  - test_load_clean_returns_dataframe
  - test_monthly_kpis_columns
  - test_quality_report_finds_at_least_n_issues
  - test_merchants_at_risk_top_n_shape
"""
from __future__ import annotations

import pandas as pd
import pytest


# Ejemplo de fixture (adáptalo a tu implementación)
@pytest.fixture
def tiny_df() -> pd.DataFrame:
    """Mini DataFrame para tests rápidos sin depender del CSV completo."""
    return pd.DataFrame(
        {
            "transaction_id": [1, 2, 3, 4],
            "merchant_id": [10, 10, 11, 11],
            "transaction_date": pd.to_datetime(
                ["2025-01-15", "2025-01-20", "2025-02-01", "2025-02-03"]
            ),
            "amount": [100.0, 50.0, 200.0, 75.0],
            "status": ["approved", "denied", "approved", "approved"],
            "channel": ["pos", "ecom", "ecom", "pos"],
        }
    )


def test_smoke_pandas_imported(tiny_df: pd.DataFrame) -> None:
    """Smoke test mínimo para confirmar que pytest detecta el módulo."""
    assert len(tiny_df) == 4


# TODO: añade tus tests reales. Ejemplos:
#
# def test_monthly_kpis_returns_one_row_per_merchant_month(tiny_df):
#     from src.parte1_pandas import monthly_kpis
#     out = monthly_kpis(tiny_df)
#     assert {"merchant_id", "month", "tpv", "approval_rate", "n_tx"}.issubset(out.columns)
#     assert len(out) == 2  # 2 merchants × 1 mes activo cada uno

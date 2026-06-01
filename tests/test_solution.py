"""
Tests para las Partes 1-3 (pandas, ML).
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from src.parte1_pandas import load_clean, merchants_at_risk, monthly_kpis, quality_report


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def tiny_df() -> pd.DataFrame:
    """Mini DataFrame limpio (post load_clean) para tests rápidos sin CSV completo."""
    return pd.DataFrame(
        {
            "transaction_id": pd.array([1, 2, 3, 4, 5], dtype="Int64"),
            "merchant_id": pd.array([10, 10, 11, 11, 10], dtype="Int64"),
            "transaction_date": pd.to_datetime(
                ["2025-08-15", "2025-08-20", "2025-09-01", "2025-09-03", "2025-08-25"]
            ),
            "amount": [100.0, 50.0, 200.0, 75.0, 80.0],
            "status": pd.Categorical(["approved", "denied", "approved", "approved", "approved"]),
            "channel": pd.Categorical(["pos", "ecom", "ecom", "pos", "pos"]),
            "segment": pd.Categorical(["SMB", "SMB", "MidMarket", "MidMarket", "SMB"]),
            "reference_date": pd.to_datetime(["2025-09-30"] * 5),
            "last_complaint_date": pd.to_datetime([None, None, "2025-09-15", None, None]),
            "fla_churn90": pd.array([0, 0, 1, 1, 0], dtype="Int8"),
            "mcc": pd.Categorical(["5411"] * 5),
            "cancellation_reason": pd.Categorical(["", "", "high_fees", "", ""]),
        }
    )


CSV_PATH = Path("data/transactions_sample.csv")


# ---------------------------------------------------------------------------
# Part 1.1 — load_clean
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not CSV_PATH.exists(), reason="CSV not available")
def test_load_clean_returns_dataframe() -> None:
    df = load_clean(CSV_PATH)
    assert isinstance(df, pd.DataFrame)
    assert len(df) > 0


@pytest.mark.skipif(not CSV_PATH.exists(), reason="CSV not available")
def test_load_clean_amount_is_numeric() -> None:
    df = load_clean(CSV_PATH)
    assert pd.api.types.is_float_dtype(df["amount"]), "amount debe ser float tras parseo BR"


@pytest.mark.skipif(not CSV_PATH.exists(), reason="CSV not available")
def test_load_clean_no_duplicates() -> None:
    df = load_clean(CSV_PATH)
    dup_cols = ["merchant_id", "transaction_date", "amount", "status", "channel"]
    assert not df.duplicated(subset=dup_cols).any(), "No deben quedar duplicados T5"


@pytest.mark.skipif(not CSV_PATH.exists(), reason="CSV not available")
def test_load_clean_transaction_date_no_nat() -> None:
    df = load_clean(CSV_PATH)
    n_nat = df["transaction_date"].isna().sum()
    assert n_nat == 0, f"Quedaron {n_nat} NaT tras parseo de fechas mixtas"


# ---------------------------------------------------------------------------
# Part 1.2 — monthly_kpis
# ---------------------------------------------------------------------------
def test_monthly_kpis_columns(tiny_df: pd.DataFrame) -> None:
    out = monthly_kpis(tiny_df)
    required = {"merchant_id", "month", "tpv", "approval_rate", "pct_ecom", "n_tx"}
    assert required.issubset(out.columns), f"Faltan columnas: {required - set(out.columns)}"


def test_monthly_kpis_shape(tiny_df: pd.DataFrame) -> None:
    out = monthly_kpis(tiny_df)
    # merchant 10: agosto (3 tx → 1 dup eliminado → 2 tx); merchant 11: septiembre (2 tx)
    # Pero tiny_df ya viene limpio (el dup de merchant 10 en agosto se mantiene como está)
    assert len(out) >= 2, "Debe haber al menos una fila por (merchant, mes)"


def test_monthly_kpis_tpv_only_approved(tiny_df: pd.DataFrame) -> None:
    out = monthly_kpis(tiny_df)
    # merchant 10 agosto: tx 1 (100 approved) + tx 2 (50 denied) + tx 5 (80 approved) → TPV=180
    m10_aug = out[(out["merchant_id"] == 10) & (out["month"].dt.month == 8)]
    assert not m10_aug.empty
    assert abs(m10_aug["tpv"].iloc[0] - 180.0) < 0.01, "TPV solo cuenta transacciones approved"


def test_monthly_kpis_approval_rate(tiny_df: pd.DataFrame) -> None:
    out = monthly_kpis(tiny_df)
    m10_aug = out[(out["merchant_id"] == 10) & (out["month"].dt.month == 8)]
    # 3 tx total (2 approved + 1 denied) → approval_rate = 2/3
    assert abs(m10_aug["approval_rate"].iloc[0] - 2 / 3) < 0.01


def test_monthly_kpis_pct_ecom_range(tiny_df: pd.DataFrame) -> None:
    out = monthly_kpis(tiny_df)
    assert out["pct_ecom"].between(0, 1).all(), "pct_ecom debe estar en [0, 1]"


# ---------------------------------------------------------------------------
# Part 1.3 — quality_report
# ---------------------------------------------------------------------------
def test_quality_report_structure(tiny_df: pd.DataFrame) -> None:
    report = quality_report(tiny_df, raw_path="nonexistent_path.csv")
    assert "issues" in report
    assert "summary" in report
    assert isinstance(report["issues"], list)
    assert report["summary"]["n_issues"] == len(report["issues"])


def test_quality_report_minimum_issues(tiny_df: pd.DataFrame) -> None:
    report = quality_report(tiny_df, raw_path="nonexistent_path.csv")
    assert len(report["issues"]) >= 5, "Debe detectar al menos 5 problemas"


def test_quality_report_issue_schema(tiny_df: pd.DataFrame) -> None:
    report = quality_report(tiny_df, raw_path="nonexistent_path.csv")
    for issue in report["issues"]:
        assert "column" in issue
        assert "rows_affected" in issue
        assert "impact" in issue
        assert "fix" in issue


@pytest.mark.skipif(not CSV_PATH.exists(), reason="CSV not available")
def test_quality_report_with_real_csv() -> None:
    df = load_clean(CSV_PATH)
    report = quality_report(df)
    assert len(report["issues"]) >= 5
    assert report["summary"]["n_rows"] > 0


# ---------------------------------------------------------------------------
# Part 1.4 — merchants_at_risk
# ---------------------------------------------------------------------------
def test_merchants_at_risk_shape(tiny_df: pd.DataFrame) -> None:
    out = merchants_at_risk(tiny_df, top_n=2)
    assert len(out) <= 2


def test_merchants_at_risk_columns(tiny_df: pd.DataFrame) -> None:
    out = merchants_at_risk(tiny_df, top_n=5)
    assert {"merchant_id", "risk_score", "top_signal"}.issubset(out.columns)


def test_merchants_at_risk_sorted_desc(tiny_df: pd.DataFrame) -> None:
    out = merchants_at_risk(tiny_df, top_n=5)
    assert out["risk_score"].is_monotonic_decreasing


def test_merchants_at_risk_score_range(tiny_df: pd.DataFrame) -> None:
    out = merchants_at_risk(tiny_df, top_n=5)
    assert out["risk_score"].between(0, 1).all()

"""
Tests para la Parte 1 · PySpark rewrite (src/parte1_pyspark.py).

Requiere el extra `pyspark` (`uv sync --extra pyspark`). Se saltan
automaticamente si pyspark no esta instalado.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

pyspark = pytest.importorskip("pyspark")

from pyspark.sql import Row, SparkSession  # noqa: E402

from src.parte1_pyspark import (  # noqa: E402
    get_spark,
    load_clean,
    merchants_at_risk,
    monthly_kpis,
    quality_report,
)

CSV_PATH = Path("data/transactions_sample.csv")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def spark() -> SparkSession:
    session = get_spark(app_name="pytest-parte1-pyspark")
    yield session
    session.stop()


@pytest.fixture
def tiny_df(spark: SparkSession):
    """Mini DataFrame limpio (post load_clean), mismo contenido que tiny_df en test_solution.py."""
    rows = [
        Row(
            transaction_id=1, merchant_id=10, transaction_date="2025-08-15", amount=100.0,
            status="approved", channel="pos", segment="SMB", reference_date="2025-09-30",
            last_complaint_date=None, fla_churn90=0,
        ),
        Row(
            transaction_id=2, merchant_id=10, transaction_date="2025-08-20", amount=50.0,
            status="denied", channel="ecom", segment="SMB", reference_date="2025-09-30",
            last_complaint_date=None, fla_churn90=0,
        ),
        Row(
            transaction_id=3, merchant_id=11, transaction_date="2025-09-01", amount=200.0,
            status="approved", channel="ecom", segment="MidMarket", reference_date="2025-09-30",
            last_complaint_date="2025-09-15", fla_churn90=1,
        ),
        Row(
            transaction_id=4, merchant_id=11, transaction_date="2025-09-03", amount=75.0,
            status="approved", channel="pos", segment="MidMarket", reference_date="2025-09-30",
            last_complaint_date=None, fla_churn90=1,
        ),
        Row(
            transaction_id=5, merchant_id=10, transaction_date="2025-08-25", amount=80.0,
            status="approved", channel="pos", segment="SMB", reference_date="2025-09-30",
            last_complaint_date=None, fla_churn90=0,
        ),
    ]
    df = spark.createDataFrame(rows)
    for col in ["transaction_date", "reference_date", "last_complaint_date"]:
        df = df.withColumn(col, df[col].cast("date"))
    return df


# ---------------------------------------------------------------------------
# Part 1.1 — load_clean
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not CSV_PATH.exists(), reason="CSV not available")
def test_load_clean_returns_dataframe(spark: SparkSession) -> None:
    df = load_clean(spark, CSV_PATH)
    assert df.count() > 0


@pytest.mark.skipif(not CSV_PATH.exists(), reason="CSV not available")
def test_load_clean_amount_is_numeric(spark: SparkSession) -> None:
    df = load_clean(spark, CSV_PATH)
    assert dict(df.dtypes)["amount"] == "double", "amount debe ser double tras parseo BR"


@pytest.mark.skipif(not CSV_PATH.exists(), reason="CSV not available")
def test_load_clean_no_duplicates(spark: SparkSession) -> None:
    df = load_clean(spark, CSV_PATH)
    dup_cols = ["merchant_id", "transaction_date", "amount", "status", "channel"]
    assert df.count() == df.dropDuplicates(dup_cols).count(), "No deben quedar duplicados T5"


@pytest.mark.skipif(not CSV_PATH.exists(), reason="CSV not available")
def test_load_clean_transaction_date_no_nulls(spark: SparkSession) -> None:
    df = load_clean(spark, CSV_PATH)
    n_null = df.filter(df["transaction_date"].isNull()).count()
    assert n_null == 0, f"Quedaron {n_null} nulls tras parseo de fechas mixtas"


# ---------------------------------------------------------------------------
# Part 1.2 — monthly_kpis
# ---------------------------------------------------------------------------
def test_monthly_kpis_columns(tiny_df) -> None:
    out = monthly_kpis(tiny_df)
    required = {"merchant_id", "month", "tpv", "approval_rate", "pct_ecom", "n_tx"}
    assert required.issubset(set(out.columns))


def test_monthly_kpis_shape(tiny_df) -> None:
    out = monthly_kpis(tiny_df)
    assert out.count() >= 2, "Debe haber al menos una fila por (merchant, mes)"


def test_monthly_kpis_tpv_only_approved(tiny_df) -> None:
    out = monthly_kpis(tiny_df).toPandas()
    out["month"] = pd.to_datetime(out["month"])
    # merchant 10 agosto: tx 1 (100 approved) + tx 2 (50 denied) + tx 5 (80 approved) -> TPV=180
    m10_aug = out[(out["merchant_id"] == 10) & (out["month"].dt.month == 8)]
    assert not m10_aug.empty
    assert abs(m10_aug["tpv"].iloc[0] - 180.0) < 0.01, "TPV solo cuenta transacciones approved"


def test_monthly_kpis_approval_rate(tiny_df) -> None:
    out = monthly_kpis(tiny_df).toPandas()
    out["month"] = pd.to_datetime(out["month"])
    m10_aug = out[(out["merchant_id"] == 10) & (out["month"].dt.month == 8)]
    # 3 tx total (2 approved + 1 denied) -> approval_rate = 2/3
    assert abs(m10_aug["approval_rate"].iloc[0] - 2 / 3) < 0.01


def test_monthly_kpis_pct_ecom_range(tiny_df) -> None:
    out = monthly_kpis(tiny_df).toPandas()
    assert out["pct_ecom"].between(0, 1).all(), "pct_ecom debe estar en [0, 1]"


# ---------------------------------------------------------------------------
# Part 1.3 — quality_report
# ---------------------------------------------------------------------------
def test_quality_report_structure(spark: SparkSession, tiny_df) -> None:
    report = quality_report(spark, tiny_df, raw_path="nonexistent_path.csv")
    assert "issues" in report
    assert "summary" in report
    assert isinstance(report["issues"], list)
    assert report["summary"]["n_issues"] == len(report["issues"])


def test_quality_report_minimum_issues(spark: SparkSession, tiny_df) -> None:
    report = quality_report(spark, tiny_df, raw_path="nonexistent_path.csv")
    assert len(report["issues"]) >= 5, "Debe detectar al menos 5 problemas"


def test_quality_report_issue_schema(spark: SparkSession, tiny_df) -> None:
    report = quality_report(spark, tiny_df, raw_path="nonexistent_path.csv")
    for issue in report["issues"]:
        assert "column" in issue
        assert "rows_affected" in issue
        assert "impact" in issue
        assert "fix" in issue


@pytest.mark.skipif(not CSV_PATH.exists(), reason="CSV not available")
def test_quality_report_with_real_csv(spark: SparkSession) -> None:
    df = load_clean(spark, CSV_PATH)
    report = quality_report(spark, df)
    assert len(report["issues"]) >= 5
    assert report["summary"]["n_rows"] > 0


# ---------------------------------------------------------------------------
# Part 1.4 — merchants_at_risk
# ---------------------------------------------------------------------------
def test_merchants_at_risk_shape(tiny_df) -> None:
    out = merchants_at_risk(tiny_df, top_n=2)
    assert out.count() <= 2


def test_merchants_at_risk_columns(tiny_df) -> None:
    out = merchants_at_risk(tiny_df, top_n=5)
    assert {"merchant_id", "risk_score", "top_signal"}.issubset(set(out.columns))


def test_merchants_at_risk_sorted_desc(tiny_df) -> None:
    out = merchants_at_risk(tiny_df, top_n=5).toPandas()
    assert out["risk_score"].is_monotonic_decreasing


def test_merchants_at_risk_score_range(tiny_df) -> None:
    out = merchants_at_risk(tiny_df, top_n=5).toPandas()
    assert out["risk_score"].between(0, 1).all()

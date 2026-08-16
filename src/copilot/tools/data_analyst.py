"""
Data Analyst tool — real SQL/KPI execution over merchant transactions.

Wraps src.parte1_pandas.load_clean()/monthly_kpis() and exposes a small,
FIXED set of parameterized query functions, adapted from the Q1-Q3 shape in
src/parte2_sql.sql (written for a hypothetical warehouse schema) to the real
data/transactions_sample.csv schema. Deliberately NOT an "LLM writes SQL"
tool: only typed, Pydantic-validated arguments get bound into hand-written
DuckDB query templates via parameter placeholders — never user text
interpolated into SQL. Letting an LLM generate free-form SQL against a live
connection is a real injection/exfiltration risk class (a prompt-injected
question could ask for a `DROP TABLE`-shaped or unbounded-scan query); fixing
the query shapes up front and only parameterizing the arguments avoids that
class entirely. See DECISIONS.md D23.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
from src.parte1_pandas import load_clean, monthly_kpis

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "data"
REAL_CSV_PATH = DATA_DIR / "transactions_sample.csv"
FIXTURE_CSV_PATH = DATA_DIR / "copilot_fixture_transactions.csv"


def default_csv_path() -> Path:
    """Prefers the real (gitignored, ~200k-row) dataset when present locally
    for a richer demo; falls back to the small committed fixture otherwise
    (e.g. in CI, or a fresh clone) — same fallback shape as
    src.parte1_pandas.quality_report's raw_path handling."""
    return REAL_CSV_PATH if REAL_CSV_PATH.exists() else FIXTURE_CSV_PATH


_CLEAN_DF_CACHE: dict[str, pd.DataFrame] = {}


def get_clean_transactions(csv_path: str | Path | None = None) -> pd.DataFrame:
    """Cached wrapper around load_clean() — keyed by resolved path so a test
    pointed at the fixture and a real run against the full CSV don't collide
    within the same process."""
    resolved = Path(csv_path) if csv_path is not None else default_csv_path()
    key = str(resolved.resolve())
    if key not in _CLEAN_DF_CACHE:
        _CLEAN_DF_CACHE[key] = load_clean(resolved)
    return _CLEAN_DF_CACHE[key]


def top_merchants_by_tpv(
    df: pd.DataFrame,
    start_date: str,
    end_date: str,
    segment: str | None = None,
    mcc: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Top merchants by approved TPV in [start_date, end_date]. Adapted from
    src/parte2_sql.sql Q1: that query filters `country = 'BR'` against a
    hypothetical `merchants` table — the real transactions_sample.csv has no
    `country` column (see data/README.md), so that filter is dropped here
    rather than silently ignored. `segment`/`mcc` filters stand in as the
    dimensions actually available on the real schema.
    """
    where = ["transaction_date BETWEEN ? AND ?"]
    params: list[Any] = [start_date, end_date]
    if segment is not None:
        where.append("segment = ?")
        params.append(segment)
    if mcc is not None:
        where.append("mcc = ?")
        params.append(mcc)
    params.append(limit)

    sql = f"""
        SELECT
            merchant_id,
            SUM(CASE WHEN status = 'approved' THEN amount ELSE 0 END) AS tpv,
            ROUND(
                SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) * 1.0 / COUNT(*), 4
            ) AS approval_rate,
            ANY_VALUE(segment) AS segment,
            ANY_VALUE(mcc) AS mcc
        FROM transactions
        WHERE {" AND ".join(where)}
        GROUP BY merchant_id
        ORDER BY tpv DESC
        LIMIT ?
    """
    con = duckdb.connect()
    try:
        con.register("transactions", df)
        result = con.execute(sql, params).fetchdf()
    finally:
        con.close()
    return result.to_dict(orient="records")


def churn_rate_by_segment(df: pd.DataFrame, min_merchants: int = 20) -> list[dict[str, Any]]:
    """% of merchants with fla_churn90=1 per segment, for segments with at
    least `min_merchants`. Adapted from src/parte2_sql.sql Q2: the real
    schema has `segment`/`fla_churn90` inline per transaction (no separate
    `merchants`/`churn_labels` tables to join), so this collapses to one row
    per merchant via DISTINCT before aggregating. `min_merchants` defaults to
    20 rather than Q2's ">=100" — the real dataset's segment cardinality
    (data/README.md) doesn't reliably clear 100 per segment the way a
    warehouse-scale table would; callers on a smaller corpus (e.g. the
    fixture) should pass an even lower value explicitly.
    """
    sql = """
        WITH per_merchant AS (
            SELECT DISTINCT merchant_id, segment, fla_churn90
            FROM transactions
        )
        SELECT
            segment,
            COUNT(*) AS n_merchants,
            SUM(fla_churn90) AS n_churned,
            ROUND(SUM(fla_churn90) * 1.0 / COUNT(*), 4) AS pct_churn
        FROM per_merchant
        GROUP BY segment
        HAVING COUNT(*) >= ?
        ORDER BY pct_churn DESC
    """
    con = duckdb.connect()
    try:
        con.register("transactions", df)
        result = con.execute(sql, [min_merchants]).fetchdf()
    finally:
        con.close()
    return result.to_dict(orient="records")


def yoy_tpv_by_month(df: pd.DataFrame, merchant_id: int) -> list[dict[str, Any]]:
    """Monthly TPV plus the same calendar month a year prior, per merchant.
    Adapted from src/parte2_sql.sql Q3, but reuses
    src.parte1_pandas.monthly_kpis() (already tested, vectorized) rather than
    hand-rolled SQL — DuckDB is used above where it adds real narrative value
    (Q1/Q2-shaped ad hoc SQL), not applied uniformly just because it's
    available. Generalizes Q3's hardcoded `prev.yr = 2024` to `prev_year =
    year - 1` so this isn't tied to one fixed year. Still an explicit
    (year-1, month-of-year) join rather than a positional LAG(12), for the
    same reason D16 gives: monthly_kpis() only returns months with actual
    transactions, so a merchant with a gap month would make a positional lag
    compare the wrong two months.
    """
    kpis = monthly_kpis(df)
    m = kpis[kpis["merchant_id"] == merchant_id][["month", "tpv"]].copy()
    if m.empty:
        return []

    m["year"] = m["month"].dt.year
    m["moy"] = m["month"].dt.month
    m["prev_year"] = m["year"] - 1

    prev = m[["year", "moy", "tpv"]].rename(columns={"year": "join_year", "tpv": "tpv_prev_year"})
    merged = m.merge(prev, left_on=["prev_year", "moy"], right_on=["join_year", "moy"], how="left")
    merged["tpv_yoy_pct"] = ((merged["tpv"] - merged["tpv_prev_year"]) / merged["tpv_prev_year"]).round(4)

    merged = merged.sort_values("month")
    out = merged[["month", "tpv", "tpv_prev_year", "tpv_yoy_pct"]].copy()
    out["month"] = out["month"].dt.strftime("%Y-%m-%d")
    out = out.astype(object).where(pd.notna(out), None)
    return out.to_dict(orient="records")

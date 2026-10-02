"""
Point-in-time XBRL fact store (DuckDB) with derived metrics that carry lineage.

Point-in-time: every query takes an optional `as_of` date and only sees fact
versions *filed* on or before it. Without this, a backtest or a "what did we
know then" question silently uses restated or not-yet-published numbers
(look-ahead bias). With as_of=None the latest filed version wins.

Lineage: a derived value (margin, leverage, YoY growth, implied Q4) is never
a bare float. It records its formula and the exact fact versions (fact_id =
ticker:metric:period:accession) it was computed from, so any number in an
answer can be traced back to specific SEC filings and recomputed.
"""
from __future__ import annotations

import json
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
from src.filings.xbrl import METRIC_LABELS, Fact

REPO_ROOT = Path(__file__).resolve().parents[2]
XBRL_DIR = REPO_ROOT / "data" / "xbrl"

_COLUMNS = (
    "ticker", "metric", "concept", "unit", "value", "period_type", "period_start", "period_end",
    "fiscal_year", "fiscal_period", "form", "accession", "filed",
)
_FLOW_METRICS = {"revenue", "net_income", "operating_income", "gross_profit", "operating_cash_flow", "capex"}


@dataclass(frozen=True)
class Derived:
    """A computed metric plus everything needed to audit it."""

    ticker: str
    name: str
    label: str
    value: float
    unit: str  # "ratio" | "USD"
    fiscal_year: int
    fiscal_period: str
    formula: str
    inputs: tuple[str, ...] = field(default_factory=tuple)  # fact_ids

    @property
    def derived_id(self) -> str:
        return f"{self.ticker}:{self.name}:{self.fiscal_period}{self.fiscal_year}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "derived_id": self.derived_id, "ticker": self.ticker, "name": self.name, "label": self.label,
            "value": self.value, "unit": self.unit, "fiscal_year": self.fiscal_year,
            "fiscal_period": self.fiscal_period, "formula": self.formula, "inputs": list(self.inputs),
        }


DERIVED_LABELS = {
    "net_margin": "Net margin",
    "operating_margin": "Operating margin",
    "gross_margin": "Gross margin",
    "debt_to_equity": "Long-term debt to equity",
    "free_cash_flow": "Free cash flow",
    "revenue_growth_yoy": "Revenue growth YoY",
    "net_income_growth_yoy": "Net income growth YoY",
}


class FactStore:
    def __init__(self) -> None:
        self._con = duckdb.connect(":memory:")
        self._lock = threading.Lock()
        # Monotonic counter bumped on every write; part of the response cache key.
        self.data_version = 0
        self._con.execute(
            """
            CREATE TABLE facts (
                ticker VARCHAR, metric VARCHAR, concept VARCHAR, unit VARCHAR, value DOUBLE,
                period_type VARCHAR, period_start DATE, period_end DATE, fiscal_year INTEGER,
                fiscal_period VARCHAR, form VARCHAR, accession VARCHAR, filed DATE,
                PRIMARY KEY (ticker, metric, fiscal_year, fiscal_period, accession)
            )
            """
        )

    # ------------------------------------------------------------------ load
    @classmethod
    def from_dir(cls, path: Path = XBRL_DIR) -> FactStore:
        store = cls()
        for f in sorted(path.glob("*.json")):
            store.upsert(Fact(**{k: v for k, v in rec.items() if k != "fact_id"}) for rec in json.loads(f.read_text()))
        return store

    def upsert(self, facts: Iterable[Fact]) -> int:
        """Idempotent: re-ingesting a filing (a redelivered stream message)
        rewrites the same rows instead of duplicating them."""
        # Bulk insert from a DataFrame: DuckDB's executemany is row-at-a-time
        # (~35s for the fixtures), a frame scan is one vectorized statement.
        batch = pd.DataFrame([{c: getattr(f, c) for c in _COLUMNS} for f in facts], columns=list(_COLUMNS))
        if batch.empty:
            return 0
        with self._lock:
            self._con.register("batch_df", batch)
            self._con.execute(
                """
                INSERT OR REPLACE INTO facts
                SELECT ticker, metric, concept, unit, value, period_type, CAST(period_start AS DATE),
                       CAST(period_end AS DATE), fiscal_year, fiscal_period, form, accession, CAST(filed AS DATE)
                FROM batch_df
                """
            )
            self._con.unregister("batch_df")
            self.data_version += 1
        return len(batch)

    def _query(self, sql: str, params: list[Any]) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._con.cursor()
            res = cur.execute(sql, params)
            cols = [d[0] for d in res.description]
            return [dict(zip(cols, row, strict=True)) for row in res.fetchall()]

    @staticmethod
    def _to_fact(row: dict[str, Any]) -> Fact:
        def iso(v: Any) -> str | None:
            return None if v is None else str(v)

        return Fact(**{**row, "period_start": iso(row["period_start"]), "period_end": str(row["period_end"]),
                       "filed": str(row["filed"])})

    # ----------------------------------------------------------------- query
    def tickers(self) -> list[str]:
        return [r["ticker"] for r in self._query("SELECT DISTINCT ticker FROM facts ORDER BY 1", [])]

    def count(self) -> int:
        return int(self._query("SELECT count(*) AS n FROM facts", [])[0]["n"])

    def history(
        self, ticker: str, metric: str, fiscal_period: str = "FY", as_of: str | None = None, n: int = 5,
        fiscal_year: int | None = None,
    ) -> list[Fact]:
        """Latest known version of each period, newest period first."""
        rows = self._query(
            """
            SELECT * EXCLUDE (rn) FROM (
                SELECT *, row_number() OVER (
                    PARTITION BY fiscal_year ORDER BY filed DESC, accession DESC) AS rn
                FROM facts
                WHERE ticker = ? AND metric = ? AND fiscal_period = ?
                  AND (CAST(? AS DATE) IS NULL OR filed <= CAST(? AS DATE))
                  AND (CAST(? AS INTEGER) IS NULL OR fiscal_year = CAST(? AS INTEGER))
            ) WHERE rn = 1 ORDER BY fiscal_year DESC LIMIT ?
            """,
            [ticker, metric, fiscal_period, as_of, as_of, fiscal_year, fiscal_year, n],
        )
        return [self._to_fact(r) for r in rows]

    def get(
        self, ticker: str, metric: str, fiscal_year: int | None = None, fiscal_period: str = "FY",
        as_of: str | None = None,
    ) -> Fact | None:
        hist = self.history(ticker, metric, fiscal_period, as_of, n=1, fiscal_year=fiscal_year)
        return hist[0] if hist else None

    def versions(self, ticker: str, metric: str, fiscal_year: int, fiscal_period: str = "FY") -> list[Fact]:
        """Every filed version of one period, oldest first (restatement audit)."""
        rows = self._query(
            "SELECT * FROM facts WHERE ticker=? AND metric=? AND fiscal_year=? AND fiscal_period=? ORDER BY filed",
            [ticker, metric, fiscal_year, fiscal_period],
        )
        return [self._to_fact(r) for r in rows]

    def latest_fiscal_year(self, ticker: str, as_of: str | None = None) -> int | None:
        f = self.get(ticker, "revenue", as_of=as_of) or self.get(ticker, "net_income", as_of=as_of)
        return f.fiscal_year if f else None

    # --------------------------------------------------------------- derived
    def derived(self, ticker: str, name: str, fiscal_year: int, as_of: str | None = None) -> Derived | None:
        def g(metric: str, fy: int = fiscal_year) -> Fact | None:
            return self.get(ticker, metric, fy, "FY", as_of)

        def ratio(num: str, den: str, formula: str) -> Derived | None:
            a, b = g(num), g(den)
            if a is None or b is None or b.value == 0:
                return None
            return Derived(ticker, name, DERIVED_LABELS[name], a.value / b.value, "ratio", fiscal_year, "FY",
                           formula, (a.fact_id, b.fact_id))

        def growth(metric: str) -> Derived | None:
            cur, prev = g(metric), g(metric, fiscal_year - 1)
            if cur is None or prev is None or prev.value == 0:
                return None
            return Derived(ticker, name, DERIVED_LABELS[name], cur.value / prev.value - 1, "ratio", fiscal_year,
                           "FY", f"{metric}[FY{fiscal_year}] / {metric}[FY{fiscal_year - 1}] - 1",
                           (cur.fact_id, prev.fact_id))

        if name == "net_margin":
            return ratio("net_income", "revenue", "net_income / revenue")
        if name == "operating_margin":
            return ratio("operating_income", "revenue", "operating_income / revenue")
        if name == "gross_margin":
            return ratio("gross_profit", "revenue", "gross_profit / revenue")
        if name == "debt_to_equity":
            return ratio("long_term_debt", "stockholders_equity", "long_term_debt / stockholders_equity")
        if name == "revenue_growth_yoy":
            return growth("revenue")
        if name == "net_income_growth_yoy":
            return growth("net_income")
        if name == "free_cash_flow":
            ocf, capex = g("operating_cash_flow"), g("capex")
            if ocf is None or capex is None:
                return None
            return Derived(ticker, name, DERIVED_LABELS[name], ocf.value - capex.value, "USD", fiscal_year, "FY",
                           "operating_cash_flow - capex", (ocf.fact_id, capex.fact_id))
        raise KeyError(f"unknown derived metric {name!r}")

    def implied_q4(self, ticker: str, metric: str, fiscal_year: int, as_of: str | None = None) -> Derived | None:
        """Q4 is rarely tagged on its own: 10-Ks report the full year. For flow
        metrics, Q4 = FY - (Q1 + Q2 + Q3), with all four inputs cited."""
        if metric not in _FLOW_METRICS:
            return None
        direct = self.get(ticker, metric, fiscal_year, "Q4", as_of)
        if direct is not None:
            return Derived(ticker, f"{metric}_q4", f"{METRIC_LABELS[metric]} (Q4)", direct.value, "USD",
                           fiscal_year, "Q4", "reported", (direct.fact_id,))
        fetched = [self.get(ticker, metric, fiscal_year, p, as_of) for p in ("FY", "Q1", "Q2", "Q3")]
        parts = [p for p in fetched if p is not None]
        if len(parts) != 4:
            return None
        fy, q1, q2, q3 = parts
        return Derived(ticker, f"{metric}_q4", f"{METRIC_LABELS[metric]} (Q4, implied)",
                       fy.value - q1.value - q2.value - q3.value, "USD", fiscal_year, "Q4",
                       "FY - Q1 - Q2 - Q3", (fy.fact_id, q1.fact_id, q2.fact_id, q3.fact_id))


_DEFAULT: FactStore | None = None
_DEFAULT_LOCK = threading.Lock()


def get_fact_store() -> FactStore:
    """Process-wide store, loaded once from the committed fixtures and then
    kept current by the streaming worker's upserts."""
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            _DEFAULT = FactStore.from_dir()
        return _DEFAULT

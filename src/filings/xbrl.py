"""
Normalizes SEC companyfacts XBRL into canonical, versioned facts.

Every reported value is kept as a *version*: the same (metric, period) is
typically reported several times (the original 10-Q/10-K, then again as a
comparative in later filings, sometimes restated). Keeping every version
with its accession number and filing date is what makes point-in-time
queries possible downstream (factstore.py): "what did the market know on
date D" must ignore values filed after D, including later restatements.

Two traps this module handles explicitly:

1. `fy`/`fp` on a companyfacts row describe the *filing*, not the period.
   A FY2025 10-K also reports FY2024 and FY2023 comparatives, all tagged
   fy=2025. Periods are therefore labeled from the filing whose own report
   period they are (the period ending on that filing's latest end date).
2. Issuers change tags over time (Apple: `Revenues` until 2018, then
   `RevenueFromContractWithCustomerExcludingAssessedTax`). Aliases resolve
   per (period, filing) in priority order: not once per company, and not
   once per period either, or the original value filed under a legacy tag
   would be shadowed by a later filing's comparative under the new tag, and
   a point-in-time query before that later filing would find nothing.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Literal

PeriodType = Literal["duration", "instant"]

# canonical metric -> (unit, us-gaap concepts in priority order)
METRICS: dict[str, tuple[str, tuple[str, ...]]] = {
    "revenue": (
        "USD",
        (
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
            "RevenuesNetOfInterestExpense",
        ),
    ),
    "net_income": ("USD", ("NetIncomeLoss", "ProfitLoss")),
    "operating_income": ("USD", ("OperatingIncomeLoss",)),
    "gross_profit": ("USD", ("GrossProfit",)),
    "operating_cash_flow": ("USD", ("NetCashProvidedByUsedInOperatingActivities",)),
    "capex": ("USD", ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets")),
    "total_assets": ("USD", ("Assets",)),
    "total_liabilities": ("USD", ("Liabilities",)),
    "stockholders_equity": (
        "USD",
        ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
    ),
    "cash": ("USD", ("CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents")),
    "long_term_debt": ("USD", ("LongTermDebt", "LongTermDebtNoncurrent")),
    "eps_diluted": ("USD/shares", ("EarningsPerShareDiluted",)),
}

METRIC_LABELS: dict[str, str] = {
    "revenue": "Revenue",
    "net_income": "Net income",
    "operating_income": "Operating income",
    "gross_profit": "Gross profit",
    "operating_cash_flow": "Operating cash flow",
    "capex": "Capital expenditures",
    "total_assets": "Total assets",
    "total_liabilities": "Total liabilities",
    "stockholders_equity": "Stockholders' equity",
    "cash": "Cash and equivalents",
    "long_term_debt": "Long-term debt",
    "eps_diluted": "Diluted EPS",
}

_ANNUAL_DAYS = (350, 380)
_QUARTER_DAYS = (80, 100)
_FORMS = ("10-K", "10-Q", "10-K/A", "10-Q/A")


@dataclass(frozen=True)
class Fact:
    ticker: str
    metric: str
    concept: str
    unit: str
    value: float
    period_type: PeriodType
    period_start: str | None
    period_end: str
    fiscal_year: int
    fiscal_period: str  # FY | Q1 | Q2 | Q3 | Q4
    form: str
    accession: str
    filed: str

    @property
    def fact_id(self) -> str:
        """Stable id of this exact version; what citations point at."""
        return f"{self.ticker}:{self.metric}:{self.fiscal_period}{self.fiscal_year}:{self.accession}"

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "fact_id": self.fact_id}


def _days(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def _duration_class(row: dict[str, Any]) -> str | None:
    if "start" not in row:
        return "instant"
    d = _days(row["start"], row["end"])
    if _ANNUAL_DAYS[0] <= d <= _ANNUAL_DAYS[1]:
        return "annual"
    if _QUARTER_DAYS[0] <= d <= _QUARTER_DAYS[1]:
        return "quarter"
    return None  # 6M/9M year-to-date: derivable, not stored


def _period_labels(rows: list[dict[str, Any]]) -> dict[tuple[str, str], tuple[int, str]]:
    """(period_end, duration_class) -> (fiscal_year, fiscal_period), taken
    only from rows where the period IS the filing's own report period."""
    report_end: dict[str, str] = {}
    for r in rows:
        if r.get("form") in _FORMS:
            report_end[r["accn"]] = max(report_end.get(r["accn"], ""), r["end"])

    labels: dict[tuple[str, str], tuple[int, str]] = {}
    for r in rows:
        cls = _duration_class(r)
        if cls is None or r.get("form") not in _FORMS or r["end"] != report_end.get(r["accn"]):
            continue
        fy, fp = int(r["fy"]), str(r["fp"])
        if cls == "quarter" and fp == "FY":
            fp = "Q4"  # a quarterly duration ending on a 10-K's report date is Q4
        elif cls == "annual":
            fp = "FY"
        elif cls == "instant" and r["form"].startswith("10-K"):
            fp = "FY"
        labels.setdefault((r["end"], cls), (fy, fp))
    return labels


def normalize_company_facts(payload: dict[str, Any], ticker: str) -> list[Fact]:
    """companyfacts JSON -> every version of every curated metric."""
    gaap = payload.get("facts", {}).get("us-gaap", {})

    all_rows = [
        r
        for unit, concepts in ((u, c) for u, c in METRICS.values())
        for concept in concepts
        for r in gaap.get(concept, {}).get("units", {}).get(unit, [])
    ]
    labels = _period_labels(all_rows)

    facts: list[Fact] = []
    for metric, (unit, concepts) in METRICS.items():
        # (period_start, period_end, accession) already served by a higher-priority alias
        claimed: set[tuple[str | None, str, str]] = set()
        for concept in concepts:
            rows = gaap.get(concept, {}).get("units", {}).get(unit, [])
            this_concept: set[tuple[str | None, str, str]] = set()
            for r in rows:
                cls = _duration_class(r)
                if cls is None or r.get("form") not in _FORMS:
                    continue
                period = (r.get("start"), r["end"], r["accn"])
                if period in claimed:
                    continue
                label = labels.get((r["end"], cls))
                if label is None:
                    continue
                this_concept.add(period)
                facts.append(
                    Fact(
                        ticker=ticker,
                        metric=metric,
                        concept=concept,
                        unit=unit,
                        value=float(r["val"]),
                        period_type="instant" if cls == "instant" else "duration",
                        period_start=r.get("start"),
                        period_end=r["end"],
                        fiscal_year=label[0],
                        fiscal_period=label[1],
                        form=r["form"],
                        accession=r["accn"],
                        filed=r["filed"],
                    )
                )
            claimed |= this_concept
    return _dedupe(facts)


def _dedupe(facts: list[Fact]) -> list[Fact]:
    """One version per (metric, period, accession): companyfacts repeats a
    row when a filing reports the same value in two contexts."""
    seen: dict[tuple[str, str | None, str, str], Fact] = {}
    for f in facts:
        seen.setdefault((f.metric, f.period_start, f.period_end, f.accession), f)
    return sorted(seen.values(), key=lambda f: (f.metric, f.period_end, f.filed, f.accession))


def restatements(facts: list[Fact]) -> dict[tuple[str, str, int], list[Fact]]:
    """Periods whose reported value changed across filings."""
    groups: dict[tuple[str, str, int], list[Fact]] = defaultdict(list)
    for f in facts:
        groups[(f.metric, f.fiscal_period, f.fiscal_year)].append(f)
    return {k: v for k, v in groups.items() if len({x.value for x in v}) > 1}

"""
Fundamentals tool: point-in-time XBRL facts and derived ratios with lineage.

Returns evidence (what the verifier checks numbers against) and findings
(pre-formatted sentences with citation markers, used verbatim by the
deterministic synthesizer and as grounding for the LLM one).
"""
from __future__ import annotations

from typing import Any

from src.copilot.entities import universe
from src.copilot.formatting import fmt_value
from src.copilot.schemas import Evidence
from src.filings.factstore import DERIVED_LABELS, Derived, FactStore
from src.filings.xbrl import METRIC_LABELS, METRICS, Fact

_RATIO_X = {"debt_to_equity"}


def sec_filing_url(cik: int, accession: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/"


def _company(ticker: str) -> str:
    name = universe().get(ticker, {}).get("display_name")
    return f"{name} ({ticker})" if name else ticker


def _lc(label: str) -> str:
    """Sentence-case a label without breaking acronyms ("Diluted EPS" -> "diluted EPS")."""
    return " ".join(w if any(c.isupper() for c in w[1:]) else w.lower() for w in label.split())


def fact_evidence(f: Fact, store: FactStore) -> Evidence:
    versions = store.versions(f.ticker, f.metric, f.fiscal_year, f.fiscal_period)
    prior = [v for v in versions if v.filed < f.filed and v.value != f.value]
    values = {"value": f.value}
    source: dict[str, Any] = {
        "concept": f"us-gaap:{f.concept}", "form": f.form, "accession": f.accession, "filed": f.filed,
        "period_start": f.period_start, "period_end": f.period_end,
        "url": sec_filing_url(int(universe()[f.ticker]["cik"]), f.accession),  # type: ignore[call-overload]
    }
    if prior:
        values["originally_reported"] = prior[0].value
        source["restated_from"] = {"accession": prior[0].accession, "filed": prior[0].filed}
    return Evidence(
        id=f.fact_id, kind="xbrl_fact", ticker=f.ticker,
        label=f"{METRIC_LABELS[f.metric]}, {f.fiscal_period} {f.fiscal_year}", values=values, unit=f.unit,
        source=source,
    )


def derived_evidence(d: Derived) -> Evidence:
    return Evidence(
        id=d.derived_id, kind="derived_metric", ticker=d.ticker, label=f"{d.label}, FY {d.fiscal_year}",
        values={"value": d.value}, unit="ratio_x" if d.name in _RATIO_X else d.unit,
        source={"formula": d.formula, "inputs": list(d.inputs)},
    )


def run_fundamentals(
    store: FactStore, tickers: list[str], metrics: list[str], fiscal_year: int | None, as_of: str | None
) -> dict[str, Any]:
    evidence: dict[str, Evidence] = {}
    findings: list[str] = []
    gaps: list[str] = []

    for ticker in tickers:
        fy = fiscal_year or store.latest_fiscal_year(ticker, as_of)
        if fy is None:
            gaps.append(f"No filings for {ticker} on or before {as_of}.")
            continue
        for metric in metrics:
            if metric in METRICS:
                fact = store.get(ticker, metric, fy, "FY", as_of)
                if fact is None:
                    gaps.append(f"{_company(ticker)} does not report {_lc(METRIC_LABELS[metric])} for FY {fy}.")
                    continue
                ev = fact_evidence(fact, store)
                evidence[ev.id] = ev
                line = f"{_company(ticker)} {_lc(METRIC_LABELS[metric])} for FY {fy} was {fmt_value(fact.value, fact.unit)}"
                if "originally_reported" in ev.values:
                    line += f" (restated; originally reported as {fmt_value(ev.values['originally_reported'], fact.unit)})"
                findings.append(f"{line} [{ev.id}].")
            elif metric in DERIVED_LABELS:
                d = store.derived(ticker, metric, fy, as_of)
                if d is None:
                    gaps.append(f"{DERIVED_LABELS[metric]} is not computable for {_company(ticker)} FY {fy} "
                                "(an input is not reported).")
                    continue
                ev = derived_evidence(d)
                evidence[ev.id] = ev
                # Input facts are evidence too: the answer may quote them.
                for fid in d.inputs:
                    t, m, period, _acc = fid.split(":")
                    f = store.get(t, m, int(period[-4:]), period[:-4], as_of)
                    if f is not None and f.fact_id == fid:
                        evidence[fid] = fact_evidence(f, store)
                # Formulas with numeric constants ("... - 1") stay in the evidence's
                # provenance: a bare constant in prose is a number with no source.
                formula = "" if any(ch.isdigit() for ch in d.formula.replace("FY", "")) else f" ({d.formula})"
                findings.append(
                    f"{_company(ticker)} {_lc(d.label)} for FY {fy} was {fmt_value(d.value, ev.unit)}{formula} [{ev.id}]."
                )
    return {
        "evidence": list(evidence.values()),
        "findings": findings,
        "gaps": gaps,
        "summary": f"{len(evidence)} fact(s)/metric(s) for {', '.join(tickers) or 'no tickers'}",
    }

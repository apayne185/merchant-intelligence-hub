"""
Deterministic entity extraction: tickers, fiscal year, metrics, weights.

Runs in both modes. In real mode the LLM router may add tickers it
recognised, but only tickers in the covered universe survive (a hallucinated
ticker cannot reach a tool).
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
UNIVERSE_PATH = REPO_ROOT / "data" / "universe.json"

_ALIASES = {
    "apple": "AAPL", "microsoft": "MSFT", "nvidia": "NVDA", "amazon": "AMZN",
    "jpmorgan": "JPM", "jp morgan": "JPM", "chase": "JPM", "goldman": "GS", "goldman sachs": "GS",
    "exxon": "XOM", "exxonmobil": "XOM", "johnson & johnson": "JNJ", "johnson and johnson": "JNJ",
    "tesla": "TSLA", "coca-cola": "KO", "coca cola": "KO", "coke": "KO",
}

# question phrase -> fact store metric (base XBRL metric or derived metric name)
METRIC_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(revenue growth|sales growth|grow(th|ing)?)\b", re.I), "revenue_growth_yoy"),
    (re.compile(r"\b(net income growth|earnings growth|profit growth)\b", re.I), "net_income_growth_yoy"),
    (re.compile(r"\b(net margin|profit margin|net profit margin)\b", re.I), "net_margin"),
    (re.compile(r"\boperating margin\b", re.I), "operating_margin"),
    (re.compile(r"\bgross margin\b", re.I), "gross_margin"),
    (re.compile(r"\b(debt.to.equity|leverage|levered|indebted)\b", re.I), "debt_to_equity"),
    (re.compile(r"\b(free cash flow|fcf)\b", re.I), "free_cash_flow"),
    (re.compile(r"\b(revenue|sales|top.line)\b", re.I), "revenue"),
    (re.compile(r"\b(net income|profit|earnings|bottom.line)\b", re.I), "net_income"),
    (re.compile(r"\b(operating income|ebit)\b", re.I), "operating_income"),
    (re.compile(r"\b(eps|earnings per share)\b", re.I), "eps_diluted"),
    (re.compile(r"\b(operating cash flow|cash from operations)\b", re.I), "operating_cash_flow"),
    (re.compile(r"\b(capex|capital expenditures?)\b", re.I), "capex"),
    (re.compile(r"\b(cash position|cash and equivalents|cash balance)\b", re.I), "cash"),
    (re.compile(r"\b(long.term debt)\b", re.I), "long_term_debt"),
    (re.compile(r"\btotal assets\b", re.I), "total_assets"),
    (re.compile(r"\b(shareholders'?|stockholders'?) equity\b", re.I), "stockholders_equity"),
]
DEFAULT_METRICS = ["revenue", "net_income", "net_margin", "revenue_growth_yoy"]

_YEAR = re.compile(r"\b(?:FY|fiscal(?: year)?\s*)?(20[0-4]\d)\b", re.I)
_WEIGHT = re.compile(r"(-?\d{1,3}(?:\.\d+)?)\s*%")


@lru_cache(maxsize=1)
def universe() -> dict[str, dict[str, object]]:
    return {c["ticker"]: c for c in json.loads(UNIVERSE_PATH.read_text())}


def resolve_ticker(token: str) -> str | None:
    t = token.strip()
    if t.upper() in universe():
        return t.upper()
    return _ALIASES.get(t.lower())


def extract_tickers(text: str) -> list[str]:
    """Tickers in order of first mention. Bare symbols must be written in
    capitals (so 'ko' in Spanish or 'gs' in a URL is not Coca-Cola/Goldman)."""
    found: list[tuple[int, str]] = []
    for sym in universe():
        for m in re.finditer(rf"(?<![A-Za-z$]){re.escape(sym)}(?![A-Za-z])", text):
            found.append((m.start(), sym))
    lowered = text.lower()
    for alias, sym in _ALIASES.items():
        for m in re.finditer(rf"\b{re.escape(alias)}\b", lowered):
            found.append((m.start(), sym))
    return list(dict.fromkeys(sym for _, sym in sorted(found)))


def extract_fiscal_year(text: str) -> int | None:
    years = [int(m.group(1)) for m in _YEAR.finditer(text)]
    return max(years) if years else None


def extract_metrics(text: str) -> list[str]:
    hits: list[str] = []
    for pattern, metric in METRIC_PATTERNS:
        if pattern.search(text) and metric not in hits:
            hits.append(metric)
    # "revenue growth" also matches "revenue": the specific form wins.
    if "revenue_growth_yoy" in hits and re.search(r"revenue growth|sales growth", text, re.I):
        hits = [h for h in hits if h != "revenue"]
    return hits


def extract_weights(text: str) -> dict[str, float]:
    """'40% AAPL, 35% in Nvidia and 25% JPM' -> {AAPL: .4, NVDA: .35, JPM: .25}."""
    weights: dict[str, float] = {}
    matches = list(_WEIGHT.finditer(text))
    for i, m in enumerate(matches):
        # The company a weight refers to is named between this "%" and the next number.
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        tail = text[m.end():end][:40]
        tickers = extract_tickers(tail)
        if tickers and tickers[0] not in weights:
            weights[tickers[0]] = float(m.group(1)) / 100.0
    return weights

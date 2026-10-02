"""
Numeric claim verification: every number in an answer must be backed by
evidence the tools produced, at the precision the answer states it.

An LLM that writes "revenue was $416.2 billion" is checked against the
XBRL fact 416,161,000,000: the claim's precision is inferred from how it is
written (one decimal place in billions = +/- $0.05B), and the claim passes
only if some evidence value falls inside that interval. "$461.2 billion" (a
transposition) fails. Percentages are checked against ratio evidence the
same way (26.9% = 0.269 +/- 0.0005).

What is deliberately not a claim: years and fiscal periods (FY2025, Q3),
and small ordinals inside words. Everything else that looks like a quantity
is checked; an unmatched number fails the answer. Over-strictness costs a
regeneration; under-strictness costs a wrong number in front of a trader.
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass

from src.copilot.schemas import Evidence, UnverifiedClaim, VerificationReport

_SCALES = {
    "trillion": 1e12, "tn": 1e12, "t": 1e12,
    "billion": 1e9, "bn": 1e9, "b": 1e9,
    "million": 1e6, "mn": 1e6, "mm": 1e6, "m": 1e6,
    "thousand": 1e3, "k": 1e3,
}

_NUMBER = re.compile(
    r"""
    (?P<neg>[-\u2212])?
    (?P<cur>\$)?
    (?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)
    \s?
    (?P<suffix>%|x\b|(?:trillion|billion|million|thousand|tn|bn|mn|mm|[tbmk])\b)?
    """,
    re.VERBOSE | re.IGNORECASE,
)
_NOT_CLAIMS = [
    re.compile(r"\b(?:FY|CY)\s?\d{2,4}\b", re.I),
    re.compile(r"\bQ[1-4]\s?(?:FY)?\s?\d{0,4}\b", re.I),
    re.compile(r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b"),          # ISO dates
    re.compile(r"\b(?:19|20)\d{2}\b(?![.,]\d|\s?%|\s?(?:billion|million))"),  # bare years
    re.compile(r"\b\d{10}-\d{2}-\d{6}\b"),                   # SEC accession numbers
    re.compile(r"\b(?:10|20|40)-[KF](?:/A)?\b|\b10-Q(?:/A)?\b|\b8-K\b", re.I),  # form types
    re.compile(r"\bItem\s+\d{1,2}[A-C]?\b", re.I),                # 10-K item numbers
    re.compile(r"\[[^\]]*\]"),                               # citation markers [AAPL:revenue:...]
]


@dataclass(frozen=True)
class Claim:
    text: str
    value: float
    tolerance: float
    kind: str  # "usd" | "pct" | "multiple" | "number"
    start: int = 0  # character span in the original text (masking preserves offsets)
    end: int = 0


def _mask(text: str) -> str:
    for pattern in _NOT_CLAIMS:
        text = pattern.sub(lambda m: " " * len(m.group(0)), text)
    return text


def extract_claims(text: str) -> list[Claim]:
    masked = _mask(text)
    claims: list[Claim] = []
    for m in _NUMBER.finditer(masked):
        raw = m.group("num")
        # Skip digits glued to letters (e.g. "10-day" stays a claim, "S&P500" doesn't).
        before = masked[m.start() - 1] if m.start() > 0 else " "
        if before.isalpha():
            continue
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        value = float(raw.replace(",", ""))
        half_ulp = 0.5 * 10 ** (-decimals)
        suffix = (m.group("suffix") or "").lower()
        sign = -1.0 if m.group("neg") else 1.0
        text = m.group(0)
        span = (m.start() + len(text) - len(text.lstrip()), m.end() - len(text) + len(text.rstrip()))
        if suffix == "%":
            claims.append(Claim(m.group(0).strip(), sign * value / 100, half_ulp / 100, "pct", *span))
        elif suffix == "x":
            claims.append(Claim(m.group(0).strip(), sign * value, half_ulp, "multiple", *span))
        elif suffix in _SCALES:
            scale = _SCALES[suffix]
            claims.append(Claim(m.group(0).strip(), sign * value * scale, half_ulp * scale, "usd", *span))
        else:
            kind = "usd" if m.group("cur") else "number"
            claims.append(Claim(m.group(0).strip(), sign * value, half_ulp, kind, *span))
    return claims


def evidence_values(evidence: Iterable[Evidence]) -> list[float]:
    vals: list[float] = []
    for ev in evidence:
        vals.extend(v for v in ev.values.values() if isinstance(v, (int, float)) and math.isfinite(v))
    return vals


def _matches(claim: Claim, values: list[float]) -> bool:
    # Relative slack of 1e-9 absorbs float representation error only.
    for v in values:
        for candidate in (v, -v) if claim.kind in ("pct", "usd", "number") else (v,):
            if abs(candidate - claim.value) <= claim.tolerance + 1e-9 * max(1.0, abs(candidate)):
                return True
    return False


def verify_answer(answer: str, evidence: Iterable[Evidence]) -> VerificationReport:
    """Sign is not checked for usd/pct/plain numbers: "fell 3.1%" and
    "growth of -3.1%" both describe the evidence value -0.031, and losses
    (VaR) are reported positive by convention."""
    claims = extract_claims(answer)
    if not claims:
        return VerificationReport(status="no_numeric_claims", numbers_checked=0, numbers_verified=0)
    values = evidence_values(evidence)
    bad = [c for c in claims if not _matches(c, values)]
    return VerificationReport(
        status="failed" if bad else "verified",
        numbers_checked=len(claims),
        numbers_verified=len(claims) - len(bad),
        unverified=[UnverifiedClaim(text=c.text, value=c.value) for c in bad],
    )

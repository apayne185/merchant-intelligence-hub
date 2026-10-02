"""
Filing search tool: retrieval over 10-K Item 1A (Risk Factors) passages.

Hybrid BM25 + dense retrieval (BM25 only in mock mode), one index per ticker
plus one over the whole universe, built lazily and cached by retrieval_core. A question naming companies searches only
their filings, so a strong lexical match in another issuer's 10-K cannot
crowd out the one asked about.

Numbers quoted in a passage become evidence values of that passage, so an
answer quoting "approximately 58% of net sales" from the filing verifies,
while the same figure invented without a source does not.
"""
from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

from src.copilot.retrieval_core import analyze, dedupe_by_field, get_index
from src.copilot.schemas import Evidence
from src.copilot.verification import extract_claims

REPO_ROOT = Path(__file__).resolve().parents[3]
FILINGS_DIR = REPO_ROOT / "data" / "filings"
EXCERPT_CHARS = 600


@cache
def _load(ticker: str | None) -> list[dict[str, Any]]:
    pattern = f"{ticker}_risk_factors.json" if ticker else "*_risk_factors.json"
    records: list[dict[str, Any]] = []
    for path in sorted(FILINGS_DIR.glob(pattern)):
        records.extend(json.loads(path.read_text()))
    return records


def search(query: str, ticker: str | None, k: int, mock: bool) -> list[dict[str, Any]]:
    index = get_index(f"risk_factors:{ticker or '*'}", lambda: _load(ticker), text_field="text", mock=mock)
    return dedupe_by_field(index.search(query, k * 2), field="text")[:k]


def _excerpt(text: str, query: str) -> str:
    """Query-focused snippet: starts at the sentence sharing the most query
    terms (passages run to ~1.2k chars and the relevant clause is often late)."""
    text = " ".join(text.split())
    if len(text) <= EXCERPT_CHARS:
        return text
    terms = set(analyze(query))
    sentences = re.split(r"(?<=[.;:])\s+", text)
    scores = [len(terms & set(analyze(s))) for s in sentences]
    best = max(range(len(sentences)), key=lambda i: (scores[i], -i))
    prefix = "..." if best else ""
    snippet = " ".join(sentences[best:])
    budget = EXCERPT_CHARS - len(prefix)
    if len(snippet) > budget:
        snippet = snippet[: budget - 3].rsplit(" ", 1)[0] + "..."
    return prefix + snippet


def run_filing_search(question: str, tickers: list[str], mock: bool, k: int = 3) -> dict[str, Any]:
    scopes: list[str | None] = list(tickers) or [None]
    per_scope = k if len(scopes) == 1 else max(1, k - 1)
    evidence: list[Evidence] = []
    findings: list[str] = []
    for scope in scopes:
        for hit in search(question, scope, per_scope, mock):
            excerpt = _excerpt(hit["text"], question)
            quoted = {f"quoted_{i}": c.value for i, c in enumerate(extract_claims(excerpt))}
            ev = Evidence(
                id=hit["id"], kind="filing_passage", ticker=hit["ticker"],
                label=f"{hit['ticker']} {hit['form']} {hit['section']}", values=quoted, excerpt=excerpt,
                source={"accession": hit["accession"], "form": hit["form"], "filed": hit["filed"],
                        "section": hit["section"]},
            )
            evidence.append(ev)
            first_sentence = excerpt.removeprefix("...").split(". ")[0].rstrip(".")
            findings.append(f"{hit['ticker']}'s {hit['form']} filed {hit['filed']} ({hit['section']}) states: "
                            f"\"{first_sentence}.\" [{ev.id}]")
    return {
        "evidence": evidence, "findings": findings, "gaps": [] if evidence else ["No matching filing passages."],
        "summary": f"retrieved {len(evidence)} risk-factor passage(s)",
    }

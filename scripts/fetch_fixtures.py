"""
Refreshes the committed real-data fixtures under data/.

    SEC_USER_AGENT="Your Name you@example.com" uv run python -m scripts.fetch_fixtures

Writes:
  data/universe.json                 tickers, CIKs, names
  data/xbrl/<TICKER>.json            normalized XBRL fact versions (FY2019+)
  data/filings/<TICKER>_risk_factors.json   Item 1A passages of the latest 10-K
  data/filings/filing_index.json     recent 10-K/10-Q filings (streaming replay source)
  data/prices/daily_adjclose.csv     5y daily adjusted closes

Tests and the MOCK_LLM demo run entirely from these files; nothing at
runtime needs network access except the live streaming poller.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
from src.filings.edgar import EdgarClient, recent_filings
from src.filings.sections import chunk_text, extract_risk_factors, html_to_text, passage_id
from src.filings.xbrl import normalize_company_facts
from src.marketdata.prices import fetch_yahoo_adjclose

DATA = Path(__file__).resolve().parents[1] / "data"
MIN_FISCAL_YEAR = 2019

UNIVERSE = [
    ("AAPL", 320193, "Technology", "Apple"),
    ("MSFT", 789019, "Technology", "Microsoft"),
    ("NVDA", 1045810, "Technology", "NVIDIA"),
    ("AMZN", 1018724, "Consumer", "Amazon"),
    ("JPM", 19617, "Financials", "JPMorgan Chase"),
    ("GS", 886982, "Financials", "Goldman Sachs"),
    ("XOM", 34088, "Energy", "ExxonMobil"),
    ("JNJ", 200406, "Healthcare", "Johnson & Johnson"),
    ("TSLA", 1318605, "Consumer", "Tesla"),
    ("KO", 21344, "Consumer", "Coca-Cola"),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-prices", action="store_true")
    args = ap.parse_args()

    client = EdgarClient.from_env()
    (DATA / "xbrl").mkdir(parents=True, exist_ok=True)
    (DATA / "filings").mkdir(parents=True, exist_ok=True)
    (DATA / "prices").mkdir(parents=True, exist_ok=True)

    universe, index = [], []
    for ticker, cik, sector, display_name in UNIVERSE:
        facts_payload = client.company_facts(cik)
        facts = [f for f in normalize_company_facts(facts_payload, ticker) if f.fiscal_year >= MIN_FISCAL_YEAR]
        (DATA / "xbrl" / f"{ticker}.json").write_text(
            json.dumps([f.to_dict() for f in facts], separators=(",", ":")) + "\n"
        )

        subs = client.submissions(cik)
        filings = recent_filings(subs)
        universe.append({"ticker": ticker, "cik": cik, "name": subs.get("name"), "sector": sector,
                         "fiscal_year_end": subs.get("fiscalYearEnd"), "display_name": display_name})
        index += [{"ticker": ticker, "cik": cik, **f} for f in filings[:8]]

        tenk = next(f for f in filings if f["form"] == "10-K")
        text = html_to_text(client.filing_document(cik, tenk["accessionNumber"], tenk["primaryDocument"]))
        risk = extract_risk_factors(text)
        passages = [
            {"id": passage_id(ticker, tenk["accessionNumber"], i, chunk), "ticker": ticker,
             "accession": tenk["accessionNumber"], "form": "10-K", "filed": tenk["filingDate"],
             "section": "Item 1A. Risk Factors", "text": chunk}
            for i, chunk in enumerate(chunk_text(risk))
        ]
        (DATA / "filings" / f"{ticker}_risk_factors.json").write_text(json.dumps(passages, indent=1) + "\n")
        print(f"{ticker}: {len(facts)} fact versions, {len(passages)} risk-factor passages "
              f"({len(risk):,} chars) from {tenk['accessionNumber']}")

    (DATA / "universe.json").write_text(json.dumps(universe, indent=2) + "\n")
    index.sort(key=lambda r: r["acceptanceDateTime"])
    (DATA / "filings" / "filing_index.json").write_text(json.dumps(index, indent=1) + "\n")

    if not args.skip_prices:
        frames = [fetch_yahoo_adjclose(t) for t, *_ in UNIVERSE]
        pd.concat(frames).to_csv(DATA / "prices" / "daily_adjclose.csv", index=False)
        print(f"prices: {sum(len(f) for f in frames):,} rows")
    client.close()


if __name__ == "__main__":
    main()

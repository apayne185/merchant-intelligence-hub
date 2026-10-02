"""
Daily price history and return construction for the risk engine.

Prices are adjusted closes (splits and dividends folded in), so a log return
across a split date is a real return, not a -75% artifact. The committed
fixture (data/prices/daily_adjclose.csv) is fetched by
scripts/fetch_fixtures.py; `fetch_yahoo_adjclose` is the only network code
and is swappable for a licensed vendor feed behind the same signature.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
PRICES_PATH = REPO_ROOT / "data" / "prices" / "daily_adjclose.csv"


@lru_cache(maxsize=1)
def load_prices(path: str = str(PRICES_PATH)) -> pd.DataFrame:
    """Wide frame: index = trading date, columns = tickers."""
    long = pd.read_csv(path, parse_dates=["date"])
    wide = long.pivot(index="date", columns="ticker", values="adj_close").sort_index()
    return wide


def log_returns(prices: pd.DataFrame, tickers: list[str], window: int, as_of: str | None = None) -> pd.DataFrame:
    """Aligned daily log returns for `tickers` over the last `window` trading
    days on or before `as_of`. Dates where any ticker is missing are dropped
    (inner alignment), so every row is a joint scenario for the portfolio."""
    missing = [t for t in tickers if t not in prices.columns]
    if missing:
        raise KeyError(f"no price history for {missing}")
    px = prices[tickers]
    if as_of is not None:
        px = px.loc[: pd.Timestamp(as_of)]
    rets: pd.DataFrame = px.apply(np.log).diff().dropna(how="any")
    return rets.tail(window)


def fetch_yahoo_adjclose(ticker: str, range_: str = "5y") -> pd.DataFrame:  # pragma: no cover - network
    import httpx

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range={range_}&interval=1d&events=split,div"
    resp = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]
    dates = pd.to_datetime(result["timestamp"], unit="s").normalize()
    adj = result["indicators"]["adjclose"][0]["adjclose"]
    df = pd.DataFrame({"date": dates.strftime("%Y-%m-%d"), "ticker": ticker, "adj_close": adj}).dropna()
    df["adj_close"] = df["adj_close"].round(6)
    return df

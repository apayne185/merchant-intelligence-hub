"""
Portfolio risk facade used by the copilot's market-risk and pre-trade agents.

Computes, for a set of positions on a given date (point-in-time: only prices
on or before `as_of` are used):
  - 1-day historical VaR/ES and Gaussian parametric VaR (C++ riskcore);
  - 10-day Monte Carlo VaR/ES with Student-t daily shocks (C++ riskcore);
  - a rolling historical-VaR backtest with Kupiec's POF test, so every VaR
    figure ships with evidence of whether the model has been calibrated;
  - Euler (component) VaR: each position's additive share of parametric VaR,
    which answers "which position is driving the risk".

Every result is tagged with a deterministic `risk_id` so the answer verifier
can tie a quoted number back to the exact computation that produced it.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from statistics import NormalDist
from typing import Any

import numpy as np
import riskcore
from src.marketdata.prices import load_prices, log_returns

TRADING_DAYS = 252


@dataclass(frozen=True)
class RiskRequest:
    tickers: tuple[str, ...]
    weights: tuple[float, ...]
    as_of: str | None = None
    window: int = 500
    confidence: float = 0.99
    horizon_days: int = 10
    mc_paths: int = 100_000
    seed: int = 7

    def __post_init__(self) -> None:
        if not self.tickers or len(self.tickers) != len(self.weights):
            raise ValueError("tickers and weights must be non-empty and the same length")
        if not 0.5 < self.confidence < 1.0:
            raise ValueError("confidence must be in (0.5, 1)")
        if not 30 <= self.window <= 2000:
            raise ValueError("window must be between 30 and 2000 trading days")

    @property
    def risk_id(self) -> str:
        blob = json.dumps(self.__dict__, sort_keys=True, default=str).encode()
        return "risk:" + hashlib.sha256(blob).hexdigest()[:12]


def portfolio_risk(req: RiskRequest) -> dict[str, Any]:
    rets = log_returns(load_prices(), list(req.tickers), window=req.window + 250, as_of=req.as_of)
    if len(rets) < req.window + 30:
        raise ValueError(f"only {len(rets)} aligned return days available, need {req.window + 30}")
    w = np.asarray(req.weights, dtype=np.float64)
    matrix = rets.to_numpy(dtype=np.float64)
    pnl_all = riskcore.portfolio_returns(matrix, w)

    est = matrix[-req.window :]
    pnl = pnl_all[-req.window :]
    mu = est.mean(axis=0)
    cov = np.cov(est, rowvar=False).reshape(len(w), len(w))

    hist = riskcore.historical_var(pnl, req.confidence)
    param = riskcore.parametric_var(pnl, req.confidence)
    mc = riskcore.monte_carlo_var(
        mu, cov, w, paths=req.mc_paths, confidence=req.confidence, horizon_days=req.horizon_days,
        seed=req.seed, dof=5,
    )
    bt = riskcore.backtest_historical_var(pnl_all, window=250, confidence=req.confidence)

    # Euler allocation of parametric VaR: VaR_i = w_i * (cov w)_i / sigma_p * |z|.
    sigma_p = math.sqrt(float(w @ cov @ w))
    z = -NormalDist().inv_cdf(1 - req.confidence)
    component = (w * (cov @ w)) / sigma_p * z if sigma_p > 0 else np.zeros_like(w)
    total = float(component.sum()) or 1.0

    vol = est.std(axis=0, ddof=1) * math.sqrt(TRADING_DAYS)
    return {
        "risk_id": req.risk_id,
        "backend": "riskcore (C++20)",
        "as_of": str(rets.index[-1].date()),
        "window_days": req.window,
        "confidence": req.confidence,
        "positions": [
            {"ticker": t, "weight": float(wi), "annualized_vol": float(v), "component_var_share": float(c / total)}
            for t, wi, v, c in zip(req.tickers, w, vol, component, strict=True)
        ],
        "historical_1d": {"var": hist["var"], "es": hist["es"]},
        "parametric_1d": {"var": param["var"], "es": param["es"]},
        "monte_carlo": {"var": mc["var"], "es": mc["es"], "horizon_days": req.horizon_days, "paths": req.mc_paths,
                        "distribution": "Student-t, 5 dof"},
        "backtest": {
            "observations": bt["observations"], "exceptions": bt["exceptions"],
            "expected_exceptions": round(bt["observations"] * (1 - req.confidence), 1),
            "kupiec_p_value": bt["kupiec_p_value"],
            "calibrated": bool(bt["kupiec_p_value"] >= 0.05),
        },
    }

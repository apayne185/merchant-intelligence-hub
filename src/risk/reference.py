"""
NumPy reference implementation of the riskcore conventions.

Two jobs: the parity oracle the C++ engine is tested against
(tests/test_riskcore_parity.py), and the vectorized baseline it is
benchmarked against (scripts/benchmark_riskcore.py). It is written to be
idiomatic, fast NumPy (np.partition, sliding_window_view), so the benchmark
measures C++ against good Python, not against a strawman loop.
"""
from __future__ import annotations

import math
from statistics import NormalDist
from typing import Any

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from numpy.typing import NDArray

F64 = NDArray[np.float64]


def tail_count(n: int, confidence: float) -> int:
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be in (0, 1)")
    if n <= 0:
        raise ValueError("need at least one scenario")
    return min(n, max(1, math.ceil((1.0 - confidence) * n - 1e-9)))


def historical_var(pnl: F64, confidence: float = 0.99) -> dict[str, Any]:
    pnl = np.asarray(pnl, dtype=np.float64)
    k = tail_count(pnl.size, confidence)
    part = np.partition(pnl, k - 1)
    return {"var": float(-part[k - 1]), "es": float(-part[:k].mean()), "scenarios": int(pnl.size)}


def parametric_var(pnl: F64, confidence: float = 0.99) -> dict[str, Any]:
    pnl = np.asarray(pnl, dtype=np.float64)
    mean, sd = float(pnl.mean()), float(pnl.std(ddof=1))
    alpha = 1.0 - confidence
    z = NormalDist().inv_cdf(alpha)
    pdf = math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)
    return {"var": -(mean + sd * z), "es": -mean + sd * pdf / alpha, "scenarios": int(pnl.size)}


def monte_carlo_var(
    mu: F64, cov: F64, weights: F64, paths: int = 100_000, confidence: float = 0.99,
    horizon_days: int = 10, seed: int = 42, dof: int = 5,
) -> dict[str, Any]:
    """Same model as the C++ engine, different random stream: results agree
    statistically (within Monte Carlo error), not bit for bit."""
    rng = np.random.default_rng(seed)
    n = len(mu)
    chol = np.linalg.cholesky(cov)
    z = rng.standard_normal((paths, horizon_days, n))
    scale = np.ones((paths, horizon_days, 1))
    if dof:
        chi2 = rng.chisquare(dof, size=(paths, horizon_days, 1))
        scale = np.sqrt((dof - 2.0) / dof) * np.sqrt(dof / chi2)
    daily = mu + scale * (z @ chol.T)
    cum = daily.sum(axis=1)
    pnl = np.expm1(cum) @ weights
    return historical_var(pnl, confidence)


def backtest_historical_var(pnl: F64, window: int = 250, confidence: float = 0.99) -> dict[str, Any]:
    pnl = np.asarray(pnl, dtype=np.float64)
    if window < 2 or pnl.size <= window:
        raise ValueError("need more observations than the window")
    k = tail_count(window, confidence)
    windows = sliding_window_view(pnl[:-1], window)  # windows[i] forecasts day i + window
    var_series = -np.partition(windows, k - 1, axis=1)[:, k - 1]
    realized = pnl[window:]
    exceptions = int((realized < -var_series).sum())
    t, x, p = realized.size, exceptions, 1.0 - confidence

    def xlogy(a: float, b: float) -> float:
        return 0.0 if a == 0 else a * math.log(b)

    ph = x / t
    lr = max(0.0, -2.0 * ((xlogy(t - x, 1 - p) + xlogy(x, p)) - (xlogy(t - x, 1 - ph) + xlogy(x, ph))))
    return {
        "observations": t, "exceptions": x, "exception_rate": ph, "kupiec_lr": lr,
        "kupiec_p_value": math.erfc(math.sqrt(lr / 2)) if lr > 0 else 1.0, "var_series": var_series,
    }

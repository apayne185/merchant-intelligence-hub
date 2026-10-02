"""
The C++ engine against its NumPy reference, plus the portfolio facade.

Deterministic estimators (historical, parametric, backtest) must agree to
floating-point precision; Monte Carlo uses a different random stream in each
implementation, so the two must agree statistically, and the C++ result must
be bit-identical across thread counts.
"""
from __future__ import annotations

import math
import threading
import time

import numpy as np
import pytest
import riskcore
from src.risk import reference
from src.risk.engine import RiskRequest, portfolio_risk


@pytest.fixture(scope="module")
def pnl() -> np.ndarray:
    rng = np.random.default_rng(1)
    return rng.standard_t(4, size=3000) * 0.01


@pytest.mark.parametrize("n,conf", [(1000, 0.99), (1000, 0.95), (250, 0.99), (7, 0.99), (12345, 0.975)])
def test_tail_count_matches_reference(n: int, conf: float) -> None:
    assert riskcore.tail_count(n, conf) == reference.tail_count(n, conf)


@pytest.mark.parametrize("conf", [0.95, 0.99, 0.999])
def test_historical_var_parity(pnl: np.ndarray, conf: float) -> None:
    a, b = riskcore.historical_var(pnl, conf), reference.historical_var(pnl, conf)
    assert a["var"] == pytest.approx(b["var"], abs=1e-15)
    assert a["es"] == pytest.approx(b["es"], rel=1e-12)


def test_parametric_var_parity(pnl: np.ndarray) -> None:
    a, b = riskcore.parametric_var(pnl, 0.99), reference.parametric_var(pnl, 0.99)
    assert a["var"] == pytest.approx(b["var"], rel=1e-10)
    assert a["es"] == pytest.approx(b["es"], rel=1e-10)


def test_backtest_parity(pnl: np.ndarray) -> None:
    a = riskcore.backtest_historical_var(pnl, 250, 0.99)
    b = reference.backtest_historical_var(pnl, 250, 0.99)
    assert a["exceptions"] == b["exceptions"] and a["observations"] == b["observations"]
    np.testing.assert_allclose(a["var_series"], b["var_series"], rtol=0, atol=1e-15)
    assert a["kupiec_p_value"] == pytest.approx(b["kupiec_p_value"], rel=1e-9)


def test_norm_ppf_matches_stdlib() -> None:
    from statistics import NormalDist

    for p in (1e-9, 0.001, 0.01, 0.3, 0.5, 0.9, 0.999):
        assert riskcore.norm_ppf(p) == pytest.approx(NormalDist().inv_cdf(p), abs=1e-12)


_MU = np.array([0.0004, 0.0002, 0.0003])
_COV = np.array([[4e-4, 1e-4, 5e-5], [1e-4, 2.5e-4, 3e-5], [5e-5, 3e-5, 1e-4]])
_W = np.array([0.5, 0.3, 0.2])


def test_monte_carlo_deterministic_across_thread_counts() -> None:
    runs = [riskcore.monte_carlo_var(_MU, _COV, _W, paths=50_000, threads=t) for t in (1, 2, 3, 8)]
    assert len({(r["var"], r["es"]) for r in runs}) == 1


def test_monte_carlo_agrees_with_numpy_reference() -> None:
    for dof in (0, 5):
        a = riskcore.monte_carlo_var(_MU, _COV, _W, paths=200_000, dof=dof, seed=3)
        b = reference.monte_carlo_var(_MU, _COV, _W, paths=200_000, dof=dof, seed=3)
        assert a["var"] == pytest.approx(b["var"], rel=0.03)
        assert a["es"] == pytest.approx(b["es"], rel=0.03)


def test_fat_tails_raise_es_relative_to_var() -> None:
    normal = riskcore.monte_carlo_var(_MU, _COV, _W, paths=200_000, dof=0)
    fat = riskcore.monte_carlo_var(_MU, _COV, _W, paths=200_000, dof=4)
    assert fat["es"] / fat["var"] > normal["es"] / normal["var"]


def test_horizon_scales_roughly_with_sqrt_time() -> None:
    one = riskcore.monte_carlo_var(np.zeros(3), _COV, _W, paths=200_000, horizon_days=1, dof=0)
    ten = riskcore.monte_carlo_var(np.zeros(3), _COV, _W, paths=200_000, horizon_days=10, dof=0)
    assert ten["var"] / one["var"] == pytest.approx(math.sqrt(10), rel=0.08)


def test_input_validation_errors_surface_as_python_exceptions() -> None:
    with pytest.raises(ValueError, match="positive definite"):
        riskcore.monte_carlo_var(np.zeros(2), np.array([[1.0, 2.0], [2.0, 1.0]]), np.array([0.5, 0.5]))
    with pytest.raises(ValueError):
        riskcore.historical_var(np.array([0.01, -0.02]), 1.5)
    with pytest.raises(ValueError):
        riskcore.monte_carlo_var(_MU, _COV, _W, dof=2)
    with pytest.raises(ValueError):
        riskcore.portfolio_returns(np.ones((3, 2)), np.ones(3))
    with pytest.raises(ValueError):
        reference.backtest_historical_var(np.ones(10), 20)


def test_engine_releases_the_gil() -> None:
    """Four concurrent MC runs on separate threads finish well under 4x a
    single run only if the GIL is released during computation."""
    def job() -> None:
        riskcore.monte_carlo_var(_MU, _COV, _W, paths=400_000, threads=1)

    t0 = time.perf_counter()
    job()
    single = time.perf_counter() - t0
    threads = [threading.Thread(target=job) for _ in range(4)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert time.perf_counter() - t0 < 3.0 * single


# ----------------------------------------------------------------- facade
def test_portfolio_risk_report() -> None:
    r = portfolio_risk(RiskRequest(("AAPL", "NVDA", "JPM"), (0.4, 0.4, 0.2), mc_paths=20_000))
    assert r["backend"].startswith("riskcore")
    assert sum(p["component_var_share"] for p in r["positions"]) == pytest.approx(1.0)
    assert r["historical_1d"]["es"] >= r["historical_1d"]["var"] > 0
    assert r["monte_carlo"]["var"] > r["historical_1d"]["var"]  # 10-day > 1-day
    assert r["backtest"]["observations"] > 0


def test_portfolio_risk_is_point_in_time() -> None:
    r = portfolio_risk(RiskRequest(("AAPL",), (1.0,), as_of="2024-06-30", mc_paths=10_000))
    assert r["as_of"] <= "2024-06-30"
    assert RiskRequest(("AAPL",), (1.0,), as_of="2024-06-30").risk_id != RiskRequest(("AAPL",), (1.0,)).risk_id


@pytest.mark.parametrize("kwargs", [
    {"tickers": (), "weights": ()},
    {"tickers": ("AAPL",), "weights": (0.5, 0.5)},
    {"tickers": ("AAPL",), "weights": (1.0,), "confidence": 1.0},
    {"tickers": ("AAPL",), "weights": (1.0,), "window": 5},
])
def test_risk_request_validation(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        RiskRequest(**kwargs)


def test_unknown_ticker_and_insufficient_history() -> None:
    with pytest.raises(KeyError):
        portfolio_risk(RiskRequest(("ZZZZ",), (1.0,)))
    with pytest.raises(ValueError, match="aligned return days"):
        portfolio_risk(RiskRequest(("AAPL",), (1.0,), as_of="2022-01-31"))

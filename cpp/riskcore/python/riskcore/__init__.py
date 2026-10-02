"""C++20 portfolio VaR / Expected Shortfall engine (pybind11 bindings).

All functions release the GIL while computing. See include/riskcore/riskcore.hpp
for the exact quantile and ES conventions.
"""
from riskcore._riskcore import (
    NotPositiveDefinite,
    backtest_historical_var,
    cholesky,
    historical_var,
    monte_carlo_var,
    norm_ppf,
    parametric_var,
    portfolio_returns,
    tail_count,
)

__all__ = [
    "NotPositiveDefinite",
    "backtest_historical_var",
    "cholesky",
    "historical_var",
    "monte_carlo_var",
    "norm_ppf",
    "parametric_var",
    "portfolio_returns",
    "tail_count",
]

// riskcore: portfolio market-risk primitives (VaR / Expected Shortfall).
//
// Conventions shared with the NumPy reference (src/risk/reference.py), which
// the parity tests hold this library to:
//   * inputs are daily log returns; P&L is expressed as a return on capital;
//   * VaR and ES are reported as positive loss fractions at `confidence`;
//   * the empirical quantile is the lower one: with n scenarios sorted
//     ascending, the tail holds k = ceil((1 - confidence) * n) scenarios
//     (with a 1e-9 guard, since 1 - 0.99 is 0.010000000000000009 in binary),
//     VaR = -p[k-1] and ES = -mean(p[0..k-1]).
#pragma once

#include <cstddef>
#include <cstdint>
#include <span>
#include <stdexcept>
#include <vector>

namespace riskcore {

struct RiskResult {
    double var;            // Value at Risk, positive = loss
    double es;             // Expected Shortfall (mean loss beyond VaR)
    std::size_t scenarios; // number of P&L scenarios the estimate used
};

struct BacktestResult {
    std::size_t observations;  // out-of-sample days tested
    std::size_t exceptions;    // days the realized loss exceeded VaR
    double exception_rate;
    double kupiec_lr;          // Kupiec proportion-of-failures LR statistic, ~chi2(1) under H0
    double kupiec_p_value;
    std::vector<double> var_series;  // the VaR forecast for each tested day
};

// Row-major T x N return matrix times weights (N) -> portfolio returns (T).
std::vector<double> portfolio_returns(std::span<const double> returns, std::size_t rows, std::size_t cols,
                                      std::span<const double> weights);

// Number of tail scenarios for a sample of size n.
std::size_t tail_count(std::size_t n, double confidence);

// Historical simulation. O(n) via std::nth_element; `pnl` is copied, not mutated.
RiskResult historical_var(std::span<const double> pnl, double confidence);

// Gaussian closed form from the sample mean/stdev of `pnl`.
RiskResult parametric_var(std::span<const double> pnl, double confidence);

// Lower-triangular Cholesky factor of a symmetric positive-definite N x N
// matrix (row-major). Throws std::domain_error if not positive definite.
std::vector<double> cholesky(std::span<const double> cov, std::size_t n);

struct MonteCarloConfig {
    std::size_t paths = 100'000;
    double confidence = 0.99;
    // Holding period in trading days. Daily log returns compound over the
    // horizon and P&L is sum_i w_i * (exp(cumulative log return_i) - 1), which
    // is nonlinear: unlike a one-day linear portfolio, no closed form exists.
    unsigned horizon_days = 10;
    std::uint64_t seed = 42;
    // Student-t degrees of freedom for fat tails; 0 means Gaussian.
    unsigned dof = 5;
    // 0 = std::thread::hardware_concurrency(). The result does not depend on it.
    unsigned threads = 0;
};

// Monte Carlo VaR/ES over `horizon_days`, with daily log returns drawn i.i.d.
// from a multivariate Normal or Student-t with mean `mu` (N) and covariance
// `cov` (N x N, the daily covariance; the t draw is rescaled so its covariance
// matches `cov`).
RiskResult monte_carlo_var(std::span<const double> mu, std::span<const double> cov, std::size_t n,
                           std::span<const double> weights, const MonteCarloConfig& cfg);

// Rolling historical-VaR backtest: for each day t >= window, forecast VaR from
// the previous `window` returns and count days where pnl[t] < -VaR.
BacktestResult backtest_historical_var(std::span<const double> pnl, std::size_t window, double confidence);

// Standard normal inverse CDF (Acklam's rational approximation, refined with
// one Halley step; |error| < 1e-15 over (0, 1)).
double norm_ppf(double p);

// Survival function of chi-square with 1 degree of freedom.
double chi2_1_sf(double x);

}  // namespace riskcore

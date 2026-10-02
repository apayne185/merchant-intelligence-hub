// Dependency-free unit tests (run by ctest, and under ASan/UBSan in CI).
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <utility>
#include <cstdio>
#include <functional>
#include <numeric>
#include <stdexcept>
#include <string>
#include <vector>

#include "riskcore/riskcore.hpp"

namespace {
int failures = 0;
int checks = 0;

void expect(bool ok, const std::string& what) {
    ++checks;
    if (!ok) {
        ++failures;
        std::fprintf(stderr, "FAIL: %s\n", what.c_str());
    }
}

void expect_near(double a, double b, double tol, const std::string& what) {
    expect(std::fabs(a - b) <= tol, what + " (" + std::to_string(a) + " vs " + std::to_string(b) + ")");
}

template <typename E>
void expect_throws(const std::function<void()>& fn, const std::string& what) {
    try {
        fn();
        expect(false, what + " did not throw");
    } catch (const E&) {
        expect(true, what);
    }
}

std::vector<double> iota_returns(int n) {
    // -n/1000, ..., -1/1000: a known, ordered loss distribution.
    std::vector<double> v;
    for (int i = n; i >= 1; --i) v.push_back(-i / 1000.0);
    return v;
}
}  // namespace

int main() {
    using namespace riskcore;

    // tail_count: the 1e-9 guard keeps 1% of 1000 at exactly 10 scenarios.
    expect(tail_count(1000, 0.99) == 10, "tail_count(1000, 0.99) == 10");
    expect(tail_count(1000, 0.95) == 50, "tail_count(1000, 0.95) == 50");
    expect(tail_count(10, 0.99) == 1, "tail_count clamps to 1");
    expect_throws<std::invalid_argument>([] { tail_count(10, 1.0); }, "confidence 1.0 rejected");

    // Historical VaR/ES on -0.001..-1.000: the 10 worst are -1.000..-0.991.
    {
        auto pnl = iota_returns(1000);
        auto r = historical_var(pnl, 0.99);
        expect_near(r.var, 0.991, 1e-12, "historical VaR");
        expect_near(r.es, (1.0 + 0.991) / 2.0, 1e-12, "historical ES");
        expect(pnl.front() == -1.0, "input not mutated");
    }

    // Large-n path (sample, filter, select) must equal a brute-force sort on
    // adversarial layouts: random, sorted, reverse-sorted, constant, duplicates.
    {
        auto brute = [](std::vector<double> v, double conf) {
            std::sort(v.begin(), v.end());
            const std::size_t k = tail_count(v.size(), conf);
            double sum = 0;
            for (std::size_t i = 0; i < k; ++i) sum += v[i];
            return std::pair{-v[k - 1], -sum / static_cast<double>(k)};
        };
        std::uint64_t x = 88172645463325252ULL;
        auto next = [&] { x ^= x << 13; x ^= x >> 7; x ^= x << 17; return static_cast<double>(x % 1'000'003) / 1e5 - 5.0; };
        std::vector<std::vector<double>> layouts(5, std::vector<double>(1'000'000));
        for (auto& v : layouts[0]) v = next();
        for (std::size_t i = 0; i < layouts[1].size(); ++i) layouts[1][i] = static_cast<double>(i);
        for (std::size_t i = 0; i < layouts[2].size(); ++i) layouts[2][i] = -static_cast<double>(i);
        std::fill(layouts[3].begin(), layouts[3].end(), -0.25);
        for (std::size_t i = 0; i < layouts[4].size(); ++i) layouts[4][i] = static_cast<double>(i % 7);
        for (std::size_t li = 0; li < layouts.size(); ++li)
            for (double conf : {0.99, 0.999, 0.95}) {
                auto r = historical_var(layouts[li], conf);
                auto [var, es] = brute(layouts[li], conf);
                expect(r.var == var, "large-n VaR exact, layout " + std::to_string(li));
                expect_near(r.es, es, 1e-9 * std::max(1.0, std::fabs(es)), "large-n ES, layout " + std::to_string(li));
            }
    }

    // Parametric VaR of a symmetric two-point sample: mean 0, sd known.
    {
        std::vector<double> pnl{-0.01, 0.01, -0.01, 0.01};
        const double sd = std::sqrt(4 * 0.0001 / 3.0);
        auto r = parametric_var(pnl, 0.99);
        expect_near(r.var, sd * 2.3263478740408408, 1e-12, "parametric VaR");
    }

    expect_near(norm_ppf(0.975), 1.959963984540054, 1e-13, "norm_ppf(0.975)");
    expect_near(norm_ppf(0.01), -2.3263478740408408, 1e-13, "norm_ppf(0.01)");
    expect_near(norm_ppf(1e-10), -6.361340902404056, 1e-10, "norm_ppf tail");

    // Cholesky reconstructs the input.
    {
        std::vector<double> cov{4, 2, 0.6, 2, 2, 0.5, 0.6, 0.5, 1};
        auto L = cholesky(cov, 3);
        for (std::size_t i = 0; i < 3; ++i)
            for (std::size_t j = 0; j < 3; ++j) {
                double s = 0;
                for (std::size_t k = 0; k < 3; ++k) s += L[i * 3 + k] * L[j * 3 + k];
                expect_near(s, cov[i * 3 + j], 1e-12, "L L^T == cov");
            }
        expect_throws<std::domain_error>([] { cholesky(std::vector<double>{1, 2, 2, 1}, 2); }, "non-PD rejected");
    }

    // Monte Carlo: deterministic across thread counts, and converges to the
    // closed form in the linear Gaussian limit (1 day, tiny vol).
    {
        std::vector<double> mu{0.0, 0.0}, cov{1e-6, 2e-7, 2e-7, 1e-6}, w{0.5, 0.5};
        MonteCarloConfig cfg;
        cfg.paths = 200'000;
        cfg.horizon_days = 1;
        cfg.dof = 0;
        cfg.threads = 1;
        auto a = monte_carlo_var(mu, cov, 2, w, cfg);
        cfg.threads = 7;
        auto b = monte_carlo_var(mu, cov, 2, w, cfg);
        expect(a.var == b.var && a.es == b.es, "MC bit-identical for 1 vs 7 threads");

        const double sigma_p = std::sqrt(0.25 * 1e-6 + 0.25 * 1e-6 + 2 * 0.25 * 2e-7);
        expect_near(a.var, 2.3263478740408408 * sigma_p, 0.03 * 2.33 * sigma_p, "MC ~ closed form (3%)");

        cfg.dof = 4;
        cfg.horizon_days = 10;
        auto t = monte_carlo_var(mu, cov, 2, w, cfg);
        expect(t.es > t.var && t.var > 0, "fat-tailed 10d: ES > VaR > 0");
        expect_throws<std::invalid_argument>(
            [&] {
                MonteCarloConfig bad;
                bad.dof = 2;
                monte_carlo_var(mu, cov, 2, w, bad);
            },
            "dof <= 2 rejected");
    }

    // Backtest: a constant series never breaches; a crash day always does.
    {
        std::vector<double> pnl(300, -0.01);
        auto r = backtest_historical_var(pnl, 250, 0.99);
        expect(r.observations == 50 && r.exceptions == 0, "flat series: no exceptions");
        pnl.back() = -0.5;
        r = backtest_historical_var(pnl, 250, 0.99);
        expect(r.exceptions == 1, "crash day is an exception");
        expect(r.kupiec_p_value > 0.0 && r.kupiec_p_value <= 1.0, "Kupiec p-value in (0, 1]");
    }

    // portfolio_returns dot product.
    {
        std::vector<double> R{0.01, 0.02, -0.03, 0.04}, w{0.25, 0.75};
        auto p = portfolio_returns(R, 2, 2, w);
        expect_near(p[0], 0.0175, 1e-15, "portfolio row 0");
        expect_near(p[1], 0.0225, 1e-15, "portfolio row 1");
    }

    std::printf("%d/%d checks passed\n", checks - failures, checks);
    return failures == 0 ? 0 : 1;
}

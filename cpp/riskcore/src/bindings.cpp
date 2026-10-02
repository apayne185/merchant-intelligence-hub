// pybind11 bindings. Every entry point copies inputs into contiguous float64
// buffers while holding the GIL, then releases it for the computation, so
// concurrent API requests (FastAPI threadpool) run risk math in parallel.
#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <span>

#include "riskcore/riskcore.hpp"

namespace py = pybind11;
using Arr = py::array_t<double, py::array::c_style | py::array::forcecast>;

namespace {

std::vector<double> to_vec(const Arr& a) { return {a.data(), a.data() + a.size()}; }

// Zero-copy view of a contiguous float64 array. Safe to read with the GIL
// released: the caller's Arr keeps the buffer alive for the whole call.
std::span<const double> view(const Arr& a) { return {a.data(), static_cast<std::size_t>(a.size())}; }

py::dict to_dict(const riskcore::RiskResult& r) {
    py::dict d;
    d["var"] = r.var;
    d["es"] = r.es;
    d["scenarios"] = r.scenarios;
    return d;
}

}  // namespace

PYBIND11_MODULE(_riskcore, m) {
    m.doc() = "C++20 portfolio VaR / Expected Shortfall engine";

    m.def(
        "portfolio_returns",
        [](const Arr& returns, const Arr& weights) {
            if (returns.ndim() != 2) throw std::invalid_argument("returns must be 2-D (days x assets)");
            const auto rows = static_cast<std::size_t>(returns.shape(0));
            const auto cols = static_cast<std::size_t>(returns.shape(1));
            auto r = to_vec(returns), w = to_vec(weights);
            std::vector<double> out;
            {
                py::gil_scoped_release release;
                out = riskcore::portfolio_returns(r, rows, cols, w);
            }
            return Arr(static_cast<py::ssize_t>(out.size()), out.data());
        },
        py::arg("returns"), py::arg("weights"));

    m.def(
        "historical_var",
        [](const Arr& pnl, double confidence) {
            riskcore::RiskResult r;
            {
                py::gil_scoped_release release;
                r = riskcore::historical_var(view(pnl), confidence);
            }
            return to_dict(r);
        },
        py::arg("pnl"), py::arg("confidence") = 0.99);

    m.def(
        "parametric_var",
        [](const Arr& pnl, double confidence) { return to_dict(riskcore::parametric_var(view(pnl), confidence)); },
        py::arg("pnl"), py::arg("confidence") = 0.99);

    m.def(
        "monte_carlo_var",
        [](const Arr& mu, const Arr& cov, const Arr& weights, std::size_t paths, double confidence,
           unsigned horizon_days, std::uint64_t seed, unsigned dof, unsigned threads) {
            const auto n = static_cast<std::size_t>(mu.size());
            auto mu_v = to_vec(mu), cov_v = to_vec(cov), w_v = to_vec(weights);
            riskcore::MonteCarloConfig cfg{paths, confidence, horizon_days, seed, dof, threads};
            riskcore::RiskResult r;
            {
                py::gil_scoped_release release;
                r = riskcore::monte_carlo_var(mu_v, cov_v, n, w_v, cfg);
            }
            return to_dict(r);
        },
        py::arg("mu"), py::arg("cov"), py::arg("weights"), py::arg("paths") = 100'000,
        py::arg("confidence") = 0.99, py::arg("horizon_days") = 10, py::arg("seed") = 42, py::arg("dof") = 5,
        py::arg("threads") = 0);

    m.def(
        "backtest_historical_var",
        [](const Arr& pnl, std::size_t window, double confidence) {
            riskcore::BacktestResult r;
            {
                py::gil_scoped_release release;
                r = riskcore::backtest_historical_var(view(pnl), window, confidence);
            }
            py::dict d;
            d["observations"] = r.observations;
            d["exceptions"] = r.exceptions;
            d["exception_rate"] = r.exception_rate;
            d["kupiec_lr"] = r.kupiec_lr;
            d["kupiec_p_value"] = r.kupiec_p_value;
            d["var_series"] = Arr(static_cast<py::ssize_t>(r.var_series.size()), r.var_series.data());
            return d;
        },
        py::arg("pnl"), py::arg("window") = 250, py::arg("confidence") = 0.99);

    m.def("cholesky", [](const Arr& cov) {
        const auto n = static_cast<std::size_t>(cov.shape(0));
        auto L = riskcore::cholesky(to_vec(cov), n);
        Arr out({static_cast<py::ssize_t>(n), static_cast<py::ssize_t>(n)});
        std::copy(L.begin(), L.end(), out.mutable_data());
        return out;
    });
    m.def("norm_ppf", &riskcore::norm_ppf);
    m.def("tail_count", &riskcore::tail_count);

    py::register_exception<std::domain_error>(m, "NotPositiveDefinite", PyExc_ValueError);
}

#include "riskcore/riskcore.hpp"

#include <algorithm>
#include <atomic>
#include <cmath>
#include <numbers>
#include <numeric>
#include <thread>

namespace riskcore {
namespace {

// ---------------------------------------------------------------- RNG
// xoshiro256** (Blackman & Vigna), seeded through splitmix64. Implemented
// here rather than using <random> distributions: std::normal_distribution's
// algorithm is unspecified, so libstdc++ and libc++ produce different streams
// from the same seed, and reproducibility is a requirement for a risk number.
inline std::uint64_t splitmix64(std::uint64_t& x) {
    std::uint64_t z = (x += 0x9E3779B97F4A7C15ULL);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
    return z ^ (z >> 31);
}

class Xoshiro256 {
public:
    explicit Xoshiro256(std::uint64_t seed) {
        for (auto& w : s_) w = splitmix64(seed);
    }
    std::uint64_t next() {
        const std::uint64_t result = rotl(s_[1] * 5, 7) * 9;
        const std::uint64_t t = s_[1] << 17;
        s_[2] ^= s_[0];
        s_[3] ^= s_[1];
        s_[1] ^= s_[2];
        s_[0] ^= s_[3];
        s_[2] ^= t;
        s_[3] = rotl(s_[3], 45);
        return result;
    }
    // Uniform in the open interval (0, 1): 53 random mantissa bits, offset by
    // half a step so log(u) is always finite.
    double uniform() { return (static_cast<double>(next() >> 11) + 0.5) * 0x1.0p-53; }

    // Marsaglia polar method: two normals per accepted pair, one log + sqrt,
    // no trigonometry (about 21% of candidate pairs are rejected).
    double normal() {
        if (has_spare_) {
            has_spare_ = false;
            return spare_;
        }
        double u, v, q;
        do {
            u = 2.0 * uniform() - 1.0;
            v = 2.0 * uniform() - 1.0;
            q = u * u + v * v;
        } while (q >= 1.0 || q == 0.0);
        const double f = std::sqrt(-2.0 * std::log(q) / q);
        spare_ = v * f;
        has_spare_ = true;
        return u * f;
    }

    // Gamma(shape, 1) by Marsaglia & Tsang (shape >= 1): about one normal and
    // one uniform per draw, versus `dof` normals for a sum-of-squares chi-square.
    double gamma(double shape) {
        const double d = shape - 1.0 / 3.0;
        const double c = 1.0 / std::sqrt(9.0 * d);
        for (;;) {
            double x, v;
            do {
                x = normal();
                v = 1.0 + c * x;
            } while (v <= 0.0);
            v = v * v * v;
            const double u = uniform();
            if (u < 1.0 - 0.0331 * x * x * x * x) return d * v;
            if (std::log(u) < 0.5 * x * x + d * (1.0 - v + std::log(v))) return d * v;
        }
    }

private:
    static std::uint64_t rotl(std::uint64_t x, int k) { return (x << k) | (x >> (64 - k)); }
    std::uint64_t s_[4]{};
    double spare_ = 0.0;
    bool has_spare_ = false;
};

void check_confidence(double c) {
    if (!(c > 0.0 && c < 1.0)) throw std::invalid_argument("confidence must be in (0, 1)");
}

// VaR/ES from the k smallest values of a scratch buffer (reordered in place).
RiskResult tail_of(std::vector<double>& v, std::size_t k, std::size_t n) {
    auto kth = v.begin() + static_cast<std::ptrdiff_t>(k - 1);
    std::nth_element(v.begin(), kth, v.end());
    // After nth_element every element before kth is <= *kth: the tail is
    // exactly [begin, kth], no sort needed.
    const double tail_sum = std::accumulate(v.begin(), kth + 1, 0.0);
    return {-*kth, -tail_sum / static_cast<double>(k), n};
}

// Exact k-smallest selection without copying or partitioning all n values,
// for the usual case of a thin tail (k << n), in the spirit of Floyd-Rivest:
//   1. take a strided sample of m values and pick a threshold slightly beyond
//      the sample's tail quantile (with a ~4-sigma binomial safety margin);
//   2. one linear pass collects every value <= threshold (about 1.5k of them);
//   3. select within the candidates.
// If the sample undershoots (fewer than k candidates), fall back to a full
// select, so the result is always exact; the sample only decides speed.
RiskResult tail_stats(std::span<const double> data, double confidence) {
    const std::size_t n = data.size();
    const std::size_t k = tail_count(n, confidence);
    constexpr std::size_t kSample = 32'768;
    if (n > 4 * kSample && k * 16 < n) {
        const std::size_t stride = n / kSample;
        std::vector<double> sample;
        sample.reserve(kSample);
        for (std::size_t i = 0; i < n && sample.size() < kSample; i += stride) sample.push_back(data[i]);
        const double expected = static_cast<double>(k) / static_cast<double>(n) * static_cast<double>(sample.size());
        const auto j = std::min(sample.size() - 1,
                                static_cast<std::size_t>(expected * 1.5 + 4.0 * std::sqrt(expected) + 8.0));
        std::nth_element(sample.begin(), sample.begin() + static_cast<std::ptrdiff_t>(j), sample.end());
        const double threshold = sample[j];
        std::vector<double> cand;
        cand.reserve(2 * k + 64);
        for (const double x : data)
            if (x <= threshold) cand.push_back(x);
        if (cand.size() >= k) return tail_of(cand, k, n);
    }
    std::vector<double> scratch(data.begin(), data.end());
    return tail_of(scratch, k, n);
}

}  // namespace

std::size_t tail_count(std::size_t n, double confidence) {
    check_confidence(confidence);
    if (n == 0) throw std::invalid_argument("need at least one scenario");
    const double raw = std::ceil((1.0 - confidence) * static_cast<double>(n) - 1e-9);
    return std::clamp<std::size_t>(static_cast<std::size_t>(raw), 1, n);
}

std::vector<double> portfolio_returns(std::span<const double> returns, std::size_t rows, std::size_t cols,
                                      std::span<const double> weights) {
    if (returns.size() != rows * cols || weights.size() != cols)
        throw std::invalid_argument("returns must be rows x cols and weights must have cols entries");
    std::vector<double> out(rows, 0.0);
    for (std::size_t t = 0; t < rows; ++t) {
        const double* r = returns.data() + t * cols;
        double acc = 0.0;
        for (std::size_t j = 0; j < cols; ++j) acc += r[j] * weights[j];
        out[t] = acc;
    }
    return out;
}

RiskResult historical_var(std::span<const double> pnl, double confidence) { return tail_stats(pnl, confidence); }

RiskResult parametric_var(std::span<const double> pnl, double confidence) {
    check_confidence(confidence);
    const std::size_t n = pnl.size();
    if (n < 2) throw std::invalid_argument("need at least two observations");
    // Welford: numerically stable single pass.
    double mean = 0.0, m2 = 0.0;
    for (std::size_t i = 0; i < n; ++i) {
        const double d = pnl[i] - mean;
        mean += d / static_cast<double>(i + 1);
        m2 += d * (pnl[i] - mean);
    }
    const double sd = std::sqrt(m2 / static_cast<double>(n - 1));
    const double alpha = 1.0 - confidence;
    const double z = norm_ppf(alpha);
    const double pdf = std::exp(-0.5 * z * z) / std::sqrt(2.0 * std::numbers::pi);
    return {-(mean + sd * z), -mean + sd * pdf / alpha, n};
}

std::vector<double> cholesky(std::span<const double> cov, std::size_t n) {
    if (cov.size() != n * n) throw std::invalid_argument("cov must be n x n");
    std::vector<double> L(n * n, 0.0);
    for (std::size_t i = 0; i < n; ++i) {
        for (std::size_t j = 0; j <= i; ++j) {
            double sum = cov[i * n + j];
            for (std::size_t k = 0; k < j; ++k) sum -= L[i * n + k] * L[j * n + k];
            if (i == j) {
                if (sum <= 0.0) throw std::domain_error("covariance matrix is not positive definite");
                L[i * n + i] = std::sqrt(sum);
            } else {
                L[i * n + j] = sum / L[j * n + j];
            }
        }
    }
    return L;
}

RiskResult monte_carlo_var(std::span<const double> mu, std::span<const double> cov, std::size_t n,
                           std::span<const double> weights, const MonteCarloConfig& cfg) {
    check_confidence(cfg.confidence);
    if (mu.size() != n || weights.size() != n) throw std::invalid_argument("mu and weights must have n entries");
    if (cfg.paths == 0 || cfg.horizon_days == 0) throw std::invalid_argument("paths and horizon_days must be > 0");
    if (cfg.dof != 0 && cfg.dof <= 2) throw std::invalid_argument("dof must be 0 (Gaussian) or > 2");

    const std::vector<double> L = cholesky(cov, n);
    // A multivariate t with dof v has covariance v/(v-2) * Sigma; rescale so
    // the simulated daily covariance equals `cov` for both distributions.
    const double t_scale = cfg.dof ? std::sqrt((cfg.dof - 2.0) / cfg.dof) : 1.0;

    // Work is split into fixed-size blocks, each with its own RNG stream
    // derived from (seed, block index). Threads pull blocks from an atomic
    // counter, so scheduling varies but every path's random numbers do not:
    // the P&L vector, and therefore VaR/ES, is identical for any thread count.
    constexpr std::size_t kBlock = 4096;
    const std::size_t n_blocks = (cfg.paths + kBlock - 1) / kBlock;
    std::vector<double> pnl(cfg.paths);
    std::atomic<std::size_t> next_block{0};

    auto worker = [&] {
        std::vector<double> z(n), cum(n);
        for (std::size_t b = next_block.fetch_add(1); b < n_blocks; b = next_block.fetch_add(1)) {
            Xoshiro256 rng(cfg.seed ^ (0xD1B54A32D192ED03ULL * (b + 1)));
            const std::size_t end = std::min(cfg.paths, (b + 1) * kBlock);
            for (std::size_t p = b * kBlock; p < end; ++p) {
                std::fill(cum.begin(), cum.end(), 0.0);
                for (unsigned h = 0; h < cfg.horizon_days; ++h) {
                    for (auto& zi : z) zi = rng.normal();
                    double scale = t_scale;
                    if (cfg.dof) {
                        // chi2(v) = 2 * Gamma(v / 2); v > 2 keeps the shape >= 1.
                        const double chi2 = 2.0 * rng.gamma(0.5 * cfg.dof);
                        scale *= std::sqrt(cfg.dof / chi2);
                    }
                    // cum += mu + scale * L z   (L lower triangular)
                    for (std::size_t i = 0; i < n; ++i) {
                        const double* Li = L.data() + i * n;
                        double lz = 0.0;
                        for (std::size_t k = 0; k <= i; ++k) lz += Li[k] * z[k];
                        cum[i] += mu[i] + scale * lz;
                    }
                }
                double v = 0.0;
                for (std::size_t i = 0; i < n; ++i) v += weights[i] * std::expm1(cum[i]);
                pnl[p] = v;
            }
        }
    };

    unsigned threads = cfg.threads ? cfg.threads : std::max(1u, std::thread::hardware_concurrency());
    threads = static_cast<unsigned>(std::min<std::size_t>(threads, n_blocks));
    std::vector<std::jthread> pool;
    pool.reserve(threads > 0 ? threads - 1 : 0);
    for (unsigned t = 1; t < threads; ++t) pool.emplace_back(worker);
    worker();
    pool.clear();  // jthread joins on destruction

    return tail_stats(pnl, cfg.confidence);
}

namespace {
// Sorted copy of the current window: each day removes the oldest value and
// inserts the newest by binary search, so the k-th smallest is an index lookup
// instead of a fresh O(window) selection.
class SortedWindow {
public:
    explicit SortedWindow(std::span<const double> init) : v_(init.begin(), init.end()) {
        std::sort(v_.begin(), v_.end());
    }
    void slide(double out, double in) {
        v_.erase(std::lower_bound(v_.begin(), v_.end(), out));
        v_.insert(std::upper_bound(v_.begin(), v_.end(), in), in);
    }
    double kth(std::size_t k) const { return v_[k - 1]; }

private:
    std::vector<double> v_;
};
}  // namespace

BacktestResult backtest_historical_var(std::span<const double> pnl, std::size_t window, double confidence) {
    check_confidence(confidence);
    if (window < 2 || pnl.size() <= window) throw std::invalid_argument("need more observations than the window");
    BacktestResult r{};
    r.observations = pnl.size() - window;
    r.var_series.reserve(r.observations);
    const std::size_t k = tail_count(window, confidence);
    SortedWindow sorted(pnl.subspan(0, window));
    for (std::size_t t = window; t < pnl.size(); ++t) {
        if (t > window) sorted.slide(pnl[t - window - 1], pnl[t - 1]);
        const double var = -sorted.kth(k);
        r.var_series.push_back(var);
        if (pnl[t] < -var) ++r.exceptions;
    }
    const double T = static_cast<double>(r.observations);
    const double x = static_cast<double>(r.exceptions);
    const double p = 1.0 - confidence;
    r.exception_rate = x / T;
    // Kupiec POF: LR = -2 ln[(1-p)^(T-x) p^x / ((1-x/T)^(T-x) (x/T)^x)], with
    // 0 * ln(0) taken as 0 at the x = 0 and x = T boundaries.
    auto xlogy = [](double a, double b) { return a == 0.0 ? 0.0 : a * std::log(b); };
    const double ph = x / T;
    const double ll_null = xlogy(T - x, 1.0 - p) + xlogy(x, p);
    const double ll_alt = xlogy(T - x, 1.0 - ph) + xlogy(x, ph);
    r.kupiec_lr = std::max(0.0, -2.0 * (ll_null - ll_alt));
    r.kupiec_p_value = chi2_1_sf(r.kupiec_lr);
    return r;
}

double norm_ppf(double p) {
    if (!(p > 0.0 && p < 1.0)) throw std::invalid_argument("p must be in (0, 1)");
    static constexpr double a[] = {-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
                                   1.383577518672690e+02,  -3.066479806614716e+01, 2.506628277459239e+00};
    static constexpr double b[] = {-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
                                   6.680131188771972e+01,  -1.328068155288572e+01};
    static constexpr double c[] = {-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
                                   -2.549732539343734e+00, 4.374664141464968e+00,  2.938163982698783e+00};
    static constexpr double d[] = {7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
                                   3.754408661907416e+00};
    constexpr double plow = 0.02425;
    double x;
    if (p < plow) {
        const double q = std::sqrt(-2 * std::log(p));
        x = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
    } else if (p <= 1 - plow) {
        const double q = p - 0.5, r = q * q;
        x = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q /
            (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1);
    } else {
        const double q = std::sqrt(-2 * std::log(1 - p));
        x = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
    }
    // One Halley refinement step against the exact CDF.
    const double e = 0.5 * std::erfc(-x / std::numbers::sqrt2) - p;
    const double u = e * std::sqrt(2 * std::numbers::pi) * std::exp(x * x / 2);
    return x - u / (1 + x * u / 2);
}

double chi2_1_sf(double x) { return x <= 0.0 ? 1.0 : std::erfc(std::sqrt(x / 2.0)); }

}  // namespace riskcore

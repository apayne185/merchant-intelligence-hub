"""
Benchmarks the C++ riskcore engine against the vectorized NumPy reference.

    uv run python -m scripts.benchmark_riskcore

The NumPy side is idiomatic and vectorized (np.partition, sliding windows,
batched matmul), so the speedups measure C++ against good Python, not a loop.
Reports the median of several runs and writes outputs/benchmark_riskcore.json.
"""
from __future__ import annotations

import json
import os
import platform
import statistics
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import riskcore
from src.risk import reference

OUT = Path(__file__).resolve().parents[1] / "outputs" / "benchmark_riskcore.json"


def bench(fn: Callable[[], Any], repeat: int = 5) -> float:
    fn()  # warm-up
    times = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    return statistics.median(times) * 1000


def main() -> None:
    rng = np.random.default_rng(0)
    pnl_1m = rng.standard_t(4, 1_000_000) * 0.01
    pnl_10y = rng.standard_t(4, 2_520) * 0.01
    n = 10
    a = rng.standard_normal((n, n)) * 0.01
    cov = a @ a.T + np.eye(n) * 1e-4
    mu, w = np.full(n, 2e-4), np.full(n, 1 / n)
    threads = os.cpu_count() or 1

    cases = [
        ("Historical VaR/ES, 1M scenarios",
         lambda: reference.historical_var(pnl_1m), lambda: riskcore.historical_var(pnl_1m), None),
        ("Rolling VaR backtest, 10y daily, 250d window",
         lambda: reference.backtest_historical_var(pnl_10y, 250), lambda: riskcore.backtest_historical_var(pnl_10y, 250), None),
        (f"Monte Carlo 10-day VaR, {n} assets, 100k paths, Student-t (1 thread)",
         lambda: reference.monte_carlo_var(mu, cov, w, paths=100_000),
         lambda: riskcore.monte_carlo_var(mu, cov, w, paths=100_000, threads=1), None),
        (f"Monte Carlo 10-day VaR, {n} assets, 100k paths, Student-t ({threads} threads)",
         lambda: reference.monte_carlo_var(mu, cov, w, paths=100_000),
         lambda: riskcore.monte_carlo_var(mu, cov, w, paths=100_000, threads=threads), None),
    ]
    rows = []
    for name, py, cpp, _ in cases:
        t_py, t_cpp = bench(py), bench(cpp)
        rows.append({"case": name, "numpy_ms": round(t_py, 2), "riskcore_ms": round(t_cpp, 2),
                     "speedup": round(t_py / t_cpp, 1)})
    report = {"machine": {"cpu": platform.processor() or platform.machine(), "cores": threads,
                          "python": platform.python_version(), "numpy": np.__version__}, "results": rows}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=1) + "\n")
    print("| Case | NumPy (ms) | riskcore C++ (ms) | Speedup |\n|---|---:|---:|---:|")
    for r in rows:
        print(f"| {r['case']} | {r['numpy_ms']} | {r['riskcore_ms']} | {r['speedup']}x |")


if __name__ == "__main__":
    main()

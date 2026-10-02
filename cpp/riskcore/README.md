# riskcore

**English** | [Español](#riskcore-1)

C++20 portfolio market-risk engine with pybind11 bindings: historical, parametric and Monte Carlo VaR / Expected Shortfall, a rolling backtest with Kupiec's test, Cholesky and an inverse normal CDF. Design and benchmark: [DECISIONS.md D7 and D8](../../DECISIONS.md#d7).

- **Exact and fast tail selection**: sample-then-filter for thin tails, full selection as fallback, so results always equal a full sort.
- **Reproducible Monte Carlo**: fixed path blocks with counter-seeded xoshiro256** streams, identical results for any thread count; own normal and gamma samplers because standard-library distributions differ between implementations.
- **GIL released** during computation; inputs are zero-copy views of NumPy arrays.

```bash
# Python extension (normally built by `uv sync` at the repository root)
uv sync --extra dev

# C++ only: unit tests, release and sanitizer builds
cmake -S . -B build/release -DRISKCORE_PYTHON=OFF -DRISKCORE_TESTS=ON -DCMAKE_BUILD_TYPE=Release
cmake --build build/release -j && ctest --test-dir build/release --output-on-failure
cmake -S . -B build/asan -DRISKCORE_PYTHON=OFF -DRISKCORE_TESTS=ON -DRISKCORE_SANITIZE=ON -DCMAKE_BUILD_TYPE=Debug
cmake --build build/asan -j && ctest --test-dir build/asan --output-on-failure
```

```python
import numpy as np, riskcore
pnl = np.random.default_rng(0).standard_t(4, 100_000) * 0.01
riskcore.historical_var(pnl, 0.99)            # {'var': ..., 'es': ..., 'scenarios': 100000}
riskcore.monte_carlo_var(mu, cov, weights, paths=100_000, horizon_days=10, dof=5, threads=0)
```

Conventions (shared with the NumPy reference in `src/risk/reference.py`): inputs are daily log returns, VaR and ES are positive loss fractions, and the tail holds `ceil((1 - confidence) * n)` scenarios.

---

# riskcore

[English](#riskcore) | **Español**

Motor de riesgo de mercado de carteras en C++20 con *bindings* pybind11: VaR / Expected Shortfall histórico, paramétrico y Monte Carlo, backtest móvil con el test de Kupiec, Cholesky y la inversa de la normal. Diseño y benchmark: [DECISIONS.md D7 y D8](../../DECISIONS.md#d7-1).

- **Selección de la cola exacta y rápida**: muestreo y filtrado para colas finas, con selección completa como respaldo, de modo que el resultado coincide siempre con una ordenación completa.
- **Monte Carlo reproducible**: bloques fijos de trayectorias con flujos xoshiro256** sembrados por contador, resultados idénticos con cualquier número de hilos; muestreadores propios de normal y gamma porque las distribuciones de la librería estándar difieren entre implementaciones.
- **GIL liberado** durante el cálculo; las entradas son vistas sin copia de los arrays de NumPy.

```bash
# Extensión de Python (normalmente la compila `uv sync` en la raíz del repositorio)
uv sync --extra dev

# Solo C++: tests unitarios, compilación release y con sanitizers
cmake -S . -B build/release -DRISKCORE_PYTHON=OFF -DRISKCORE_TESTS=ON -DCMAKE_BUILD_TYPE=Release
cmake --build build/release -j && ctest --test-dir build/release --output-on-failure
cmake -S . -B build/asan -DRISKCORE_PYTHON=OFF -DRISKCORE_TESTS=ON -DRISKCORE_SANITIZE=ON -DCMAKE_BUILD_TYPE=Debug
cmake --build build/asan -j && ctest --test-dir build/asan --output-on-failure
```

```python
import numpy as np, riskcore
pnl = np.random.default_rng(0).standard_t(4, 100_000) * 0.01
riskcore.historical_var(pnl, 0.99)            # {'var': ..., 'es': ..., 'scenarios': 100000}
riskcore.monte_carlo_var(mu, cov, weights, paths=100_000, horizon_days=10, dof=5, threads=0)
```

Convenciones (compartidas con la referencia en NumPy de `src/risk/reference.py`): las entradas son rentabilidades logarítmicas diarias, el VaR y el ES son fracciones de pérdida positivas y la cola contiene `ceil((1 - confidence) * n)` escenarios.

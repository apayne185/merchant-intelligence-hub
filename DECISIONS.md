# Design decisions

**English** | [Español](#decisiones-de-diseño)

Every decision below answers four questions: **what I did** (the concrete action), **why** (the criterion or evidence, not "best practice"), **what I rejected** (the alternatives and why not), and **what I assumed** (what I would confirm with a stakeholder). Where building or testing exposed a bug, it is recorded under **found while building**, because those are the parts that show whether a design was actually exercised.

| | Data and correctness | | Risk engine | | Platform |
|---|---|---|---|---|---|
| [D1](#d1) | Product scope | [D7](#d7) | C++ engine and its benchmark | [D15](#d15) | API security and audit |
| [D2](#d2) | Real data, committed fixtures | [D8](#d8) | Monte Carlo model | [D16](#d16) | Observability |
| [D3](#d3) | XBRL normalization | [D9](#d9) | Backtest and attribution | [D17](#d17) | Container image |
| [D4](#d4) | Point-in-time fact store | [D10](#d10) | Pre-trade check | [D18](#d18) | Kubernetes |
| [D5](#d5) | Numeric answer verification | [D11](#d11) | Event-driven ingestion | [D19](#d19) | CI/CD and quality gates |
| [D6](#d6) | Hybrid retrieval | [D12](#d12) | Cache and Redis policy | [D20](#d20) | AWS ECS (Terraform) |
| | | [D13](#d13) | Agent orchestration | [D21](#d21) | Dependency maintenance |
| | | [D14](#d14) | Evaluation | [D22](#d22) | What is deliberately not built |

---

## D1

### Product scope: a filings and risk copilot, not a trading bot

- **What I did**: Built a research copilot over SEC filings and portfolio risk with four tools (fundamentals, filing search, market risk, pre-trade check), real public data, and a hard rule that every number in an answer is verified against its source.
- **Why**: The audience is a FinTech team building AI into proprietary trading systems. What such a team needs from an LLM is trust in its numbers, freedom from look-ahead bias, and risk figures with evidence of calibration. Those are verifiable properties on real data. A "trading copilot" fed by mocked order books and news would demonstrate none of them, and its first interview question ("where does the order book come from?") has no good answer.
- **What I rejected**: An alpha-generation and order-routing agent on simulated market feeds (indefensible data, no way to measure correctness); claiming sub-15 ms LLM inference (not measurable on this hardware, and LLM latency is not where this system's correctness lives).
- **What I assumed**: That a team evaluating LLMs for trading values a system that refuses to state an unsupported number over one that answers more questions.

## D2

### Real SEC data, committed as fixtures, fetched under SEC fair access

- **What I did**: `scripts/fetch_fixtures.py` pulls XBRL company facts, the latest 10-K (Item 1A) and the filing index for 10 issuers across 5 sectors, plus 5 years of daily adjusted prices, and commits them under `data/`. The EDGAR client (`src/filings/edgar.py`) enforces SEC's policy itself: a contact User-Agent read from `SEC_USER_AGENT` (it refuses to run without an email in it), a thread-safe token bucket at 10 requests per second, and retries with full-jitter exponential backoff on 429/5xx only.
- **Why**: Tests, CI and the mock-LLM demo must run offline and deterministically, while still exercising real data with real quirks (restatements, tag changes, fiscal years that do not match calendar years). A 404 is an answer and is not retried; only transient failures are.
- **What I rejected**: Synthetic financials (would hide exactly the XBRL problems in D3); fetching at test time (flaky, and hammering a public service from CI); storing the raw 4 MB companyfacts payloads (normalized versions from FY2019 are 2.4 MB for all 10 issuers).
- **What I assumed**: That 10 issuers are enough to exercise every code path (banks without gross profit, a June fiscal year, a January fiscal year, stock splits, a restatement) and that the universe would be widened by configuration, not code.

## D3

### XBRL normalization: two traps handled explicitly

- **What I did**: `src/filings/xbrl.py` turns companyfacts into canonical, versioned facts. (1) Fiscal labels come from the filing whose own report period a value is, not from the row's `fy`/`fp` fields. (2) Concept aliases (for example `RevenueFromContractWithCustomerExcludingAssessedTax`, then `Revenues`) resolve in priority order **per period and per filing**. Quarterly durations ending on a 10-K's report date are labeled Q4; year-to-date durations are dropped.
- **Why**: In companyfacts, `fy` describes the filing: a FY2025 10-K reports FY2024 and FY2023 comparatives also tagged `fy=2025`. Using it naively assigns three different years' revenue to FY2025. Issuers also change tags (Apple used `Revenues` until 2018).
- **Found while building**: My first alias rule resolved per period. A unit test with a legacy tag in an old filing and the new tag in a later comparative showed the original value disappeared, so a point-in-time query before the later filing found *no* revenue at all. Resolving per (period, filing) keeps every filed version. The fix changed Apple's fact count from 794 to 845 versions.
- **What I rejected**: SEC's `frames` API (calendar-aligned, which mislabels non-December fiscal years); hand-mapping each issuer (does not scale).
- **What I assumed**: That a period seen only as a comparative, with no filing of its own in the data, should be left out rather than guessed. This is pinned by a test.

## D4

### A point-in-time fact store with lineage

- **What I did**: `src/filings/factstore.py` holds every filed version of every fact in DuckDB, keyed by (ticker, metric, fiscal year, period, accession). Every query takes `as_of` and sees only versions filed on or before it; without it, the latest filing wins. Derived metrics (margins, leverage, free cash flow, YoY growth, implied Q4 = FY - Q1 - Q2 - Q3) are objects carrying their formula and the exact fact ids they were computed from. Upserts are idempotent and bump a `data_version` counter.
- **Why**: "What did we know on date D" is the core question of any backtest, and a store that keeps only the latest value silently answers it with restated numbers. The real data has examples: Apple's FY2019 diluted EPS was filed as $11.89 and restated to $2.97 after the 2020 split; Tesla's FY2024 capex was $11.339B in the original 10-K and $11.342B in the next one, which changes free cash flow depending on `as_of`. Both are golden-set cases.
- **Found while building**: The first load used DuckDB `executemany` and took 35 s for 6.8k rows (row-at-a-time). A bulk insert from a DataFrame takes 0.7 s.
- **What I rejected**: Postgres (an extra service for read-mostly, in-memory-sized data); keeping only the latest value (look-ahead bias by construction).
- **What I assumed**: That filing date is the right "knowledge" timestamp. EDGAR's acceptance time is more precise for intraday use and is carried in the ingestion events (D11).

## D5

### Numeric answer verification: verify, regenerate once, then fall back

- **What I did**: `src/copilot/verification.py` extracts every quantity from the answer (currency with scale, percentages, multiples, plain numbers), infers its precision from how it is written ("$416.2 billion" means plus or minus $0.05B), and accepts it only if some evidence value falls in that interval. Years, ISO dates, fiscal periods, accession numbers, form types and citation markers are not claims. In real mode, a failed draft is regenerated once with the exact unsupported numbers named in the prompt; if it fails again, the answer is replaced by the deterministic template built from the same evidence, and `fallback_used` is set.
- **Why**: Instructions to "only use provided numbers" reduce hallucinations but do not eliminate them; a check does. Precision-aware matching is what lets "$416.2B" pass and "$416.3B" (a 0.03% error) fail. Numbers quoted inside a retrieved 10-K passage become evidence of that passage, so quoting the filing is allowed and inventing a figure is not.
- **Found while building**: The verifier rejected my own template twice. "Kupiec test at the 5% level" stated a significance level that was not recorded as evidence (it had passed only because another 0.05 happened to be present), and a growth formula string "revenue[FY2024] / revenue[FY2023] - 1" contained a bare constant. Both were fixed in the template, not by loosening the verifier.
- **What I rejected**: An LLM judge (non-deterministic, costs a call, and cannot be gated in CI); exact string matching (rejects every legitimate rounding).
- **What I assumed**: That sign is not checked for amounts and percentages ("fell 3.1%" and "growth of -3.1%" describe the same value, and VaR is reported as a positive loss), and that over-strictness (a regeneration) is the right failure direction. Dates and entity names are not verified yet (README, limitations).

## D6

### Retrieval: hybrid BM25 + dense with Reciprocal Rank Fusion

- **What I did**: `src/copilot/retrieval_core.py` implements Okapi BM25 over postings lists (light suffix stemming, stop words plus question scaffolding words) and fuses it with dense embeddings (OpenAI or Azure OpenAI) by Reciprocal Rank Fusion (k = 60) in real mode; mock mode uses BM25 alone. One index per issuer plus one for the whole universe, so a question about NVIDIA searches only NVIDIA's 10-K. Excerpts are query-focused: they start at the sentence sharing the most query terms.
- **Why**: Dense embeddings handle paraphrase; legal text is decided by rare exact terms ("talc", "Section 232", "export controls") that embeddings average away. RRF combines ranks, so the two scores never need calibrating against each other. At 833 passages, brute-force scoring is sub-millisecond, so an ANN index would add an operational dependency for no measurable gain.
- **Found while building**: The eval's retrieval hit rate was 78% with my first lexical setup. Two causes: an inconsistent stemmer ("regulation" became "regul" while "regulatory" became "regulat", so they never matched; "mention" became "ment") and question words like "does" carrying the highest IDF in the query. Separately, the right JNJ passage ranked first but its relevant clause ("talc") was at character 990, beyond the 600-character excerpt. After fixing the stemmer, the stop list and the snippet selection: 100%. Rewriting BM25 without scikit-learn and SciPy also removed about 195 MB from the image.
- **What I rejected**: TF-IDF cosine (what the mock path used before: weak on short queries); a vector database (D6's scale note); cross-encoder reranking (a model download for offline mode).
- **What I assumed**: That Item 1A is the right first corpus for risk questions; MD&A would be the next section to index.

## D7

### The C++ engine, and the benchmark that first said it was slower

- **What I did**: `cpp/riskcore` is a C++20 library with pybind11 bindings, built by scikit-build-core as a uv workspace member, so `uv sync` compiles it. It provides historical, parametric and Monte Carlo VaR/ES, a rolling backtest with Kupiec's test, Cholesky and an inverse normal CDF. Inputs are zero-copy views of NumPy buffers, and the GIL is released during computation. A NumPy reference implementation (`src/risk/reference.py`) is both the parity oracle (deterministic estimators agree to floating-point precision) and the benchmark baseline, and it is written as good vectorized NumPy, not as a loop.
- **Why**: Risk checks sit on the request path of the pre-trade tool and must run concurrently across API threads; that needs compute that releases the GIL and uses all cores. The role asks for Python plus another OO language, and the boundary between them (memory ownership across the binding, GIL, determinism) is where hybrid systems actually break.
- **Found while building**: The first benchmark showed the C++ engine **slower** than NumPy for historical VaR (21 ms vs 4 ms) and the backtest (0.5x). Profiling showed three causes: libstdc++'s `std::nth_element` is about twice as slow as NumPy's introselect on this input; the bindings copied every input twice (16 MB of fresh, page-faulted allocations for 1M scenarios); and the backtest re-selected each 250-day window from scratch. Fixes: a sample-then-filter selection for thin tails (a threshold from a 32k strided sample with a 4-sigma margin, one linear pass, then selection among about 1.5k candidates, with a full-select fallback so the result is always exact); zero-copy `std::span` views; and a sliding sorted window for the backtest. Result: 2-3x faster on historical VaR, about 8x on the backtest, about 1.2x on single-threaded Monte Carlo and 7-8x with 8 threads. Adversarial C++ tests (sorted, reverse-sorted, constant and heavily duplicated inputs at 1M values) check the fast path against a full sort.
- **What I rejected**: Hand-written SIMD intrinsics (`-O3` auto-vectorizes the inner loops, and `-march=native` would make the wheel non-portable); Numba (does not demonstrate a second language or binding design); reporting only the favourable numbers.
- **What I assumed**: That the benchmark machine (a 4-core laptop with thermal throttling) gives representative ratios but not absolute times, so the README reports ranges.

## D8

### Monte Carlo model: multi-day horizon, Student-t shocks, reproducible across thread counts

- **What I did**: Daily log returns are drawn from a multivariate Normal or Student-t (5 degrees of freedom, rescaled so its covariance equals the sample covariance), compounded over a 10-day horizon, with P&L = sum of w_i (exp(cumulative return_i) - 1). Paths are generated in fixed blocks of 4096, each with its own xoshiro256** stream seeded from (seed, block index); threads pull blocks from an atomic counter. Normals use the Marsaglia polar method; the chi-square draw uses Marsaglia-Tsang gamma sampling.
- **Why**: For a one-day linear portfolio under an elliptical distribution, portfolio P&L is univariate and Monte Carlo just reproduces a closed form, so implementing it would be decoration. Compounding over 10 days makes P&L nonlinear and sums of t shocks are not t, so simulation is needed. Reproducibility is a requirement for a risk number: the result is bit-identical for 1, 2, 3 and 8 threads (tested). Standard-library distributions were avoided because `std::normal_distribution` differs between libstdc++ and libc++.
- **Found while building**: The first sampler used Box-Muller and a chi-square as a sum of 5 squared normals: 15 normals per path-day, with trigonometric calls. The polar method and gamma sampling cut that to about 11 and removed the trigonometry.
- **What I rejected**: Gaussian-only Monte Carlo (equivalent to parametric VaR for this portfolio); GARCH or filtered historical simulation (the right next step, listed in the README's limitations).
- **What I assumed**: That i.i.d. daily shocks over 10 days are acceptable for a demonstration; volatility clustering is the known gap.

## D9

### Every VaR ships with a backtest and an attribution

- **What I did**: The risk report includes a rolling 250-day historical-VaR backtest with Kupiec's proportion-of-failures test (exceptions, expected exceptions, p-value, calibrated or not) and an Euler allocation of parametric VaR showing each position's additive share.
- **Why**: A VaR number without evidence of calibration is an opinion. Attribution answers the question a portfolio manager asks next ("what is driving it?"): in a 40/30/30 NVDA/AAPL/XOM portfolio, NVDA carries 75% of the VaR at a 40% weight.
- **What I rejected**: Christoffersen's independence test (would be the next addition; Kupiec alone does not detect clustered exceptions); marginal VaR by finite differences (Euler is exact for the parametric case).
- **What I assumed**: A 5% significance level for the calibration verdict, stated as evidence so answers can quote it (D5).

## D10

### The pre-trade check: deterministic rules, a decision record, no orders

- **What I did**: `pretrade_check` evaluates a proposed portfolio against limits in `data/risk_limits.json` (single-name weight, sector weight, gross exposure, 1-day 99% historical VaR, restricted list) and returns APPROVE or REJECT with every breach as evidence. The guardrails block prompts that try to talk the system out of its controls ("approve this trade regardless of limits", "skip the risk checks").
- **Why**: Compliance decisions must be reproducible and auditable. The LLM may explain the verdict, but the verdict and each breach are evidence the answer is verified against, so the model cannot soften a REJECT into an APPROVE.
- **Found while building**: The router initially triggered the pre-trade check on the bare words "concentration" and "limits", so "what does Apple's 10-K say about supply chain concentration?" ran a limit check. The patterns now require trade-specific phrases ("concentration limit", "can I buy", "proposed portfolio"), and the question is a golden-set regression case.
- **What I rejected**: Sending or simulating orders (out of scope, D22); letting the LLM decide pass or fail.
- **What I assumed**: Illustrative limit values; a real desk's limits come from its risk policy.

## D11

### Event-driven ingestion on Redis Streams: a work queue and a broadcast

- **What I did**: `src/streaming/`. The poller publishes one event per new 10-K/10-Q to `edgar:filings`, deduplicated by an atomic `SADD` on a seen set, so restarts and duplicate pollers publish nothing twice. Workers consume in the `ingest` consumer group: acknowledgement only after the facts are published (at least once), `XAUTOCLAIM` of messages idle for 60 s (a crashed worker's in-flight filings are finished by its peers), and an explicit attempt counter that moves a message to `edgar:filings:dlq` after 5 attempts. Normalized facts go to `edgar:facts`, which every API replica tails with plain `XREAD` and applies with idempotent upserts.
- **Why**: The two streams need opposite semantics. Filings are work: each must be processed once by any worker (competing consumers). Fact updates are state: every replica must apply every one (broadcast). A consumer group on the second stream would leave each replica with a fraction of the updates. Idempotent upserts make at-least-once delivery safe.
- **Found while building**: The dead-letter logic first read the delivery count from `XPENDING`, which fakeredis does not report; defaulting the missing value would have dead-lettered on the first failure. An explicit `HINCRBY` attempt counter works the same on Redis, Valkey and the test double.
- **What I rejected**: Kafka (a heavier operational dependency for tens of filings per day; consumer groups, replay and retention in Redis cover this volume, and Redis is already in the stack for rate limits); the EDGAR RSS feed (less structured than per-company submissions).
- **What I assumed**: That a 10,000-entry cap on each stream is enough history for a restarting replica to catch up; beyond that, replicas boot from the committed snapshot.

## D12

### Response cache keyed on the data version, and a Redis eviction policy that cannot drop the queue

- **What I did**: The `/ask` cache key includes the redacted question, the request context (tickers, `as_of`, portfolio), locale, mode, app version and the fact store's `data_version`. Redis runs with `volatile-lru` eviction and AOF persistence.
- **Why**: Before streaming, the data was a static snapshot and the app version alone invalidated the cache. Now a 10-Q can land at any time, and an answer cached before it must not be served after it. The previous `allkeys-lru` policy was right for a cache-only Redis but would silently evict the filings stream under memory pressure; `volatile-lru` only evicts keys with a TTL (cache entries, rate-limit counters).
- **What I rejected**: A separate Redis for streams (better isolation at scale, unnecessary here, noted for production); time-based cache expiry alone (serves stale answers until the TTL runs out).
- **What I assumed**: That no tool applies per-caller authorization, so the key does not include the caller; tenant-scoped data would require the tenant in the key first.

## D13

### Orchestration: LangGraph control flow, Agno LLM calls, a closed tool set

- **What I did**: LangGraph owns state and control flow; LLM calls inside nodes go through Agno. The router computes the full ordered tool list once and each tool node pops itself off the front (a bounded worker queue). Tool names are a closed `Literal` set, the real-mode router can only add tickers that exist in the covered universe, and tools never execute model-generated code or SQL.
- **Why**: A bounded queue gives deterministic ordering and a fixed number of LLM calls per request (router, synthesis, at most one retry) regardless of how many tools fire. Entity extraction is deterministic in both modes, so a hallucinated ticker cannot reach a tool.
- **What I rejected**: A ReAct loop (unbounded LLM calls and tool choices per request); parallel fan-out (merge conflicts for no latency need at these tool timings, which are 2-20 ms each); a checkpointer (single-turn Q&A).
- **What I assumed**: That single-turn questions cover the use case; follow-up questions would need conversation state.

## D14

### Evaluation: independent ground truth, an adversarial verifier test, hard floors in CI

- **What I did**: `data/golden_set.json` has 37 cases (fundamentals, derived metrics, point-in-time, restatements, honest gaps, retrieval, risk, pre-trade, mixed routes). Expected figures were taken from SEC companyfacts by concept and period end date with a separate script, so the eval does not grade the normalizer against itself. `scripts/evaluate_copilot.py` runs every case through the real FastAPI app, then corrupts each number of each correct answer (from -10% to +50%, and digit transpositions) and measures how many corrupted answers the verifier rejects. `scripts/check_eval_floors.py` fails CI below the floors.
- **Why**: A golden set alone measures the happy path; the adversarial test measures the safety mechanism directly (442 corruptions, 100% caught, 0 false positives on correct answers). Under the mock LLM everything is deterministic, so any drop is a regression, not noise.
- **Found while building**: The first adversarial run reported 97.9% recall. All misses were bugs in the harness, not the verifier: it located claims with `str.find`, which found the "10" inside the date "2026-10-01" instead of the claim, and corrupted the date. Claims now carry exact character spans.
- **What I rejected**: LLM-graded answers (non-deterministic, cannot gate CI); generating expected values from the fact store (circular).
- **What I assumed**: That mock-mode metrics prove the pipeline and the verifier, not the model; the real-model run (`--real`) records time to first token, tokens and cost per answer but needs an API key.

## D15

### API security, guardrails and the audit log

- **What I did**: Bearer JWT validation (RS256 through the identity provider's JWKS, or HS256 for local use) with an explicit algorithm allowlist that never includes `none`, required `exp`/`sub`, audience, issuer and scope checks; the service refuses to start with `APP_ENV=production` and authentication off. PII is redacted before the router, the LLM, the cache key, logs and the audit log (cards must pass Luhn, SSN ranges the SSA never issues are excluded, Spanish DNI/NIE, IBAN, email, phone numbers of 9-15 digits so ISO dates survive). Prompt-injection patterns in English and Spanish, plus attempts to bypass the pre-trade controls, return 400. Rate limiting is a fixed window per subject (or IP) in Redis, applied after authentication so a 401 never consumes quota. Every request writes an audit row (subject, outcome, route, latency, PII counts, request and trace ids) with only the SHA-256 of the redacted question, never the text.
- **Why**: Validated rules are auditable and deterministic, which keeps CI's mock mode deterministic; the real security boundary is architectural (closed tool set, rule-based verdicts, verified numbers). Rate limits and the audit log fail open so a Redis or Postgres outage degrades protection instead of taking `/ask` down.
- **Found while building**: (1) FastAPI drops `BackgroundTasks` when an endpoint raises, which lost exactly the `blocked` and `error` audit rows an auditor most wants; those paths now return a response with the background task instead of raising. (2) `CREATE TABLE IF NOT EXISTS` is not atomic in Postgres: two concurrent first writes raced and one died on a `pg_class` unique violation. A transaction-scoped advisory lock fixed it; the regression test (4 replicas x 8 concurrent writes on a fresh table) fails 3 out of 3 times without the fix.
- **What I rejected**: LLM-based guardrails such as NeMo Guardrails or Llama Guard (an extra model call per request, non-deterministic, and no real traffic to measure false positives on); a synchronous, fail-closed audit write (right for payments, wrong for an analytical tool).
- **What I assumed**: That token validation belongs in the service even behind an API gateway (defence in depth, and the validated subject keys rate limits and the audit log).

## D16

### Observability: metrics, traces, logs and alerts for an LLM system

- **What I did**: Prometheus metrics for HTTP (RED by handler and exact status), per-graph-node latency histograms, tool invocations, numeric verification outcomes (verified, failed, fallback), LLM time to first token (measured by streaming the synthesis call), tokens and estimated cost, guardrail events, cache hits, ingest processing time and filing lag, and the fact-store version per replica. OpenTelemetry spans per node under a root span per request, exported through a Collector to Jaeger and to Prometheus via span metrics. JSON logs carry request and trace ids. Alerts include verification degradation, high time to first token, dead-lettered filings, ingest lag and **replica divergence** (API replicas disagreeing on fact-store version for 10 minutes, meaning one stopped applying updates and is serving stale numbers).
- **Why**: For an LLM feature, "is it up" is not enough: the operational questions are whether answers are still verifying, how long users wait for the first token, and whether every replica has the latest filings. Histograms rather than summaries, because quantiles aggregate across replicas only from buckets.
- **Found while building**: The middleware's SERVER span ends after `/ask` has popped its request trace from the in-process buffer, which would have recreated a span-buffer leak; the buffer ignores SERVER spans, with a regression test.
- **What I rejected**: The OpenTelemetry FastAPI auto-instrumentation package (another dependency for about 60 lines that also needed the buffer filter); a gRPC exporter (adds a native dependency to the image for no gain at this volume).
- **What I assumed**: That list prices are an acceptable cost estimate; the metric is labeled as an estimate, and a provider-reported cost is used when present.

## D17

### Container image: a C++ builder stage and three traps

- **What I did**: A multi-stage Dockerfile. The builder installs g++ and compiles riskcore through scikit-build-core (CMake and Ninja come from PyPI); the runtime stage receives only the virtual environment, the source and the data snapshot. Base images are pinned by tag and digest, the runtime runs as UID 10001 with a read-only root filesystem and no capabilities, bytecode is precompiled, the health check uses the standard library (no curl), and the base image's unused pip is removed.
- **Found while building**: (1) The first image crashed at import: uv installs workspace members *editable* by default, so the virtual environment pointed at `/app/cpp/riskcore`, a path that exists only in the builder. `--no-editable` fixed it, and `--no-install-project` avoids a second copy of the `src` package in site-packages shadowing the real one. The CI smoke test, which runs the image under Kubernetes constraints, is what caught it. (2) The compiled extension depends on libstdc++, which the slim runtime does not guarantee; it is linked statically (`-static-libstdc++ -static-libgcc`). (3) Removing scikit-learn and SciPy (D6) cut about 195 MB; the warm resident memory measured in the container is 226 MiB.
- **What I rejected**: Shipping the compiler in the runtime image; publishing riskcore as a separate wheel to a registry (the right step once a second service consumes it); distroless (the virtual environment links to the base image's interpreter).
- **What I assumed**: x86-64 deployment targets (both stages pin `linux/amd64`, matching the Fargate task in D20).

## D18

### Kubernetes: the API and the ingestion pipeline as separate workloads

- **What I did**: Kustomize base and a local overlay. The API Deployment keeps the hardened pod spec (restricted Pod Security, read-only root filesystem, dropped capabilities, seccomp, startup, liveness and readiness probes, a pre-stop delay, zone and node spread, HPA and PDB). Ingestion adds a worker Deployment (2 replicas, metrics on port 9102) and a single poller, both with their own NetworkPolicy allowing only DNS, Redis and HTTPS to sec.gov. Readiness fails on an empty fact store.
- **Why**: Workers are stateless competing consumers and scale on their own; tying them to API replicas would scale ingestion with query traffic, which is unrelated. The local overlay has no Redis, so it scales ingestion to zero and docker-compose runs the full streaming stack instead.
- **What I rejected**: Helm (Kustomize covers base and overlays without templating); Redis as a StatefulSet in the namespace (production uses a managed service with persistence and a volatile eviction policy, documented in the Secret example).
- **What I assumed**: The CIDRs in the NetworkPolicies and `FORWARDED_ALLOW_IPS` are placeholders to adjust per cluster.

## D19

### CI/CD and quality gates

- **What I did**: GitHub Actions jobs for lint (ruff, lockfile freshness, the no-em-dash house rule), mypy (strict on infra, filings, risk, streaming, market data and the verifier), security (Bandit with zero findings, Trivy filesystem scan), **C++** (warnings-as-errors build with `-Wall -Wextra -Wpedantic -Wconversion`, ctest in release and under AddressSanitizer + UndefinedBehaviorSanitizer), tests with an 85% branch-coverage gate (94% measured), the eval and its floors, an informational benchmark in the job summary, integration tests against real Redis and Postgres service containers (including the streams pipeline), deployment config validation (kubeconform, promtool, otelcol, compose), and a container job (build, smoke test under Kubernetes runtime constraints that asserts the answer verified, Trivy image gate). On `main`: publish to GHCR with SBOM and SLSA provenance, cosign keyless signing.
- **Why**: Each gate catches a class of failure the others cannot: sanitizers catch out-of-bounds and undefined behavior that unit tests miss, the smoke test caught the editable-install crash (D17), and the eval floors catch regressions in answer quality.
- **What I rejected**: Third-party wrapper actions for scanners (aquasecurity/trivy-action's tags were hijacked in 2026 to steal CI secrets; scanners run as digest-pinned official images and every action is pinned to a commit SHA); making the benchmark a gate (shared CI runners are too noisy for timing thresholds).
- **What I assumed**: That branch protection requires the `test` job, whose id is kept stable for that reason.

## D20

### AWS ECS via Terraform

- **What I did**: `terraform/` deploys the API on ECS Fargate behind an ALB in a minimal VPC (two public subnets, no NAT gateway), with an ECR repository, CloudWatch logs with explicit retention, an execution role without a task role (the app makes no AWS API calls), and an optional Secrets Manager secret for the LLM key. It is validated (`fmt`, `init`, `validate`) but not applied; nothing costs money until `terraform apply`.
- **Why**: It shows the same image running on a managed container service with least-privilege IAM, and the cost is bounded (about $35-58 a month if left running, mostly the ALB and Fargate).
- **What I rejected**: A NAT gateway (about $33 a month on its own, avoided with public subnets and tight security groups); EKS for a single service (the Kubernetes manifests cover that path).
- **What I assumed**: A demo-scale deployment: one task, local state. A shared environment needs a remote backend with locking and the ingestion workers as additional ECS services.

## D21

### Dependency maintenance: two red pull requests and a red main

- **What I did**: Fixed the failing CI on `main` and two Dependabot pull requests. `main` failed both Trivy gates on HIGH CVEs in transitive dependencies (urllib3 2.7.0 and virtualenv 21.7.3), fixed by upgrading them in the lockfile. PR #60 (uvicorn 0.54.0) failed `uv lock --check` because Dependabot edited the package entry but not the project's recorded requirements; it was superseded by a proper relock. PR #57 (Python 3.14 base image) failed because `requires-python` is `<3.14`, so the image had no compatible interpreter; Dependabot now ignores Python minor and major bumps of the base image, while patch and digest updates still flow.
- **Why**: A Python minor version is a runtime migration (wheels, C extensions, the compiled engine), not a routine bump, and should be a deliberate pull request.
- **What I rejected**: Merging PR #57 by widening `requires-python` without testing the stack on 3.14.
- **What I assumed**: That the CI services (Redis, Postgres) should track the versions in docker-compose; they were aligned (8.10 and 18.6).

## D22

### What is deliberately not built

- **No order routing or execution.** The pre-trade check returns a decision record.
- **No live market data feed.** Prices are a daily snapshot from a public endpoint for demonstration; the provider sits behind one function (`src/marketdata/prices.py`) so a licensed feed replaces it.
- **No claims about model serving latency** (vLLM, TensorRT-LLM). The system's latency budget is dominated by the hosted LLM call, which is measured (time to first token) rather than assumed.
- **No options or nonlinear instruments, and no volatility clustering** in the risk model.
- **No verification of dates and names**, only of quantities (D5).

Each of these is a scope decision with a stated next step, not an oversight.

---

# Decisiones de diseño

[English](#design-decisions) | **Español**

Cada decisión responde a cuatro preguntas: **qué hice** (la acción concreta), **por qué** (el criterio o la evidencia, no "buenas prácticas"), **qué descarté** (las alternativas y por qué no) y **qué supuse** (lo que confirmaría con un responsable real). Cuando construir o probar algo destapó un error, queda registrado en **encontrado al construirlo**, porque esas son las partes que demuestran que un diseño se ejercitó de verdad.

| | Datos y corrección | | Motor de riesgo | | Plataforma |
|---|---|---|---|---|---|
| [D1](#d1-1) | Alcance del producto | [D7](#d7-1) | Motor C++ y su benchmark | [D15](#d15-1) | Seguridad de la API y auditoría |
| [D2](#d2-1) | Datos reales, fixtures en el repo | [D8](#d8-1) | Modelo Monte Carlo | [D16](#d16-1) | Observabilidad |
| [D3](#d3-1) | Normalización XBRL | [D9](#d9-1) | Backtest y atribución | [D17](#d17-1) | Imagen de contenedor |
| [D4](#d4-1) | Almacén de hechos point-in-time | [D10](#d10-1) | Control pre-trade | [D18](#d18-1) | Kubernetes |
| [D5](#d5-1) | Verificación numérica de respuestas | [D11](#d11-1) | Ingesta orientada a eventos | [D19](#d19-1) | CI/CD y controles de calidad |
| [D6](#d6-1) | Recuperación híbrida | [D12](#d12-1) | Caché y política de Redis | [D20](#d20-1) | AWS ECS (Terraform) |
| | | [D13](#d13-1) | Orquestación de agentes | [D21](#d21-1) | Mantenimiento de dependencias |
| | | [D14](#d14-1) | Evaluación | [D22](#d22-1) | Lo que no se construye a propósito |

---

## D1

### Alcance: un copiloto de informes y riesgo, no un bot de trading

- **Qué hice**: Un copiloto de investigación sobre informes de la SEC y riesgo de cartera con cuatro herramientas (fundamentales, búsqueda en informes, riesgo de mercado, control pre-trade), datos públicos reales y una regla estricta: cada número de una respuesta se verifica contra su fuente.
- **Por qué**: El público es un equipo FinTech que integra IA en sistemas de trading propietarios. Lo que ese equipo necesita de un LLM es confiar en sus cifras, ausencia de sesgo de anticipación y cifras de riesgo con evidencia de calibración. Son propiedades verificables sobre datos reales. Un "copiloto de trading" alimentado con libros de órdenes y noticias simulados no demostraría ninguna, y su primera pregunta de entrevista ("¿de dónde sale el libro de órdenes?") no tiene buena respuesta.
- **Qué descarté**: Un agente de generación de alfa y enrutado de órdenes sobre datos de mercado simulados (datos indefendibles, sin forma de medir la corrección); prometer inferencia LLM por debajo de 15 ms (no medible en este hardware, y la latencia del LLM no es donde reside la corrección de este sistema).
- **Qué supuse**: Que un equipo que evalúa LLM para trading valora más un sistema que se niega a afirmar una cifra sin respaldo que uno que responde a más preguntas.

## D2

### Datos reales de la SEC, versionados como fixtures y descargados respetando la política de la SEC

- **Qué hice**: `scripts/fetch_fixtures.py` descarga los hechos XBRL, el último 10-K (Item 1A) y el índice de informes de 10 emisores de 5 sectores, más 5 años de precios diarios ajustados, y los guarda en `data/`. El cliente EDGAR (`src/filings/edgar.py`) aplica la política de la SEC por sí mismo: User-Agent con contacto leído de `SEC_USER_AGENT` (no arranca sin un email), un *token bucket* seguro entre hilos a 10 peticiones por segundo y reintentos con *backoff* exponencial con *jitter* completo solo ante 429/5xx.
- **Por qué**: Los tests, la CI y la demo con LLM simulado deben funcionar sin red y de forma determinista, y a la vez ejercitar datos reales con sus particularidades (reexpresiones, cambios de etiqueta, años fiscales que no coinciden con el natural). Un 404 es una respuesta y no se reintenta; solo los fallos transitorios.
- **Qué descarté**: Estados financieros sintéticos (ocultarían justo los problemas de XBRL de D3); descargar en tiempo de test (inestable, y castiga un servicio público desde la CI); guardar los JSON completos de 4 MB (las versiones normalizadas desde FY2019 ocupan 2,4 MB para los 10 emisores).
- **Qué supuse**: Que 10 emisores bastan para ejercitar cada camino del código (bancos sin beneficio bruto, un año fiscal que cierra en junio, otro en enero, *splits*, una reexpresión) y que el universo se amplía por configuración, no por código.

## D3

### Normalización XBRL: dos trampas tratadas explícitamente

- **Qué hice**: `src/filings/xbrl.py` convierte *companyfacts* en hechos canónicos y versionados. (1) Las etiquetas fiscales salen del informe cuyo periodo propio es ese valor, no de los campos `fy`/`fp` de la fila. (2) Los alias de conceptos (por ejemplo `RevenueFromContractWithCustomerExcludingAssessedTax` y después `Revenues`) se resuelven por prioridad **por periodo y por informe**. Las duraciones trimestrales que terminan en la fecha de un 10-K se etiquetan como Q4; las acumuladas del año se descartan.
- **Por qué**: En *companyfacts*, `fy` describe el informe: un 10-K de FY2025 incluye comparativos de FY2024 y FY2023 también marcados con `fy=2025`. Usarlo sin más asigna a FY2025 los ingresos de tres años distintos. Los emisores también cambian de etiqueta (Apple usó `Revenues` hasta 2018).
- **Encontrado al construirlo**: Mi primera regla de alias resolvía por periodo. Un test unitario con una etiqueta antigua en un informe viejo y la nueva en un comparativo posterior mostró que el valor original desaparecía, de modo que una consulta *point-in-time* anterior al informe posterior no encontraba *ningún* ingreso. Resolver por (periodo, informe) conserva todas las versiones presentadas. El arreglo llevó a Apple de 794 a 845 versiones de hechos.
- **Qué descarté**: La API `frames` de la SEC (alineada con el año natural, etiqueta mal los años fiscales que no cierran en diciembre); mapear cada emisor a mano (no escala).
- **Qué supuse**: Que un periodo que solo aparece como comparativo, sin informe propio en los datos, debe omitirse en lugar de adivinarse. Hay un test que lo fija.

## D4

### Un almacén de hechos point-in-time con trazabilidad

- **Qué hice**: `src/filings/factstore.py` guarda en DuckDB todas las versiones presentadas de cada hecho, con clave (ticker, métrica, año fiscal, periodo, número de registro). Cada consulta acepta `as_of` y solo ve versiones presentadas hasta esa fecha; sin él, gana el último informe. Las métricas derivadas (márgenes, apalancamiento, flujo de caja libre, crecimiento interanual, Q4 implícito = FY - Q1 - Q2 - Q3) son objetos con su fórmula y los identificadores exactos de los hechos de los que salen. Las inserciones son idempotentes e incrementan un contador `data_version`.
- **Por qué**: "¿Qué sabíamos en la fecha D?" es la pregunta central de cualquier backtest, y un almacén que solo guarda el último valor la responde, sin avisar, con cifras reexpresadas. Los datos reales tienen ejemplos: el BPA diluido de Apple de FY2019 se presentó como 11,89 $ y se reexpresó a 2,97 $ tras el *split* de 2020; el capex de Tesla de FY2024 fue de 11.339 M$ en el 10-K original y de 11.342 M$ en el siguiente, lo que cambia el flujo de caja libre según `as_of`. Ambos son casos del golden set.
- **Encontrado al construirlo**: La primera carga usaba `executemany` de DuckDB y tardaba 35 s para 6.800 filas (fila a fila). Una inserción masiva desde un DataFrame tarda 0,7 s.
- **Qué descarté**: Postgres (un servicio más para datos de solo lectura que caben en memoria); guardar solo el último valor (sesgo de anticipación por construcción).
- **Qué supuse**: Que la fecha de presentación es la marca de "conocimiento" adecuada. La hora de aceptación de EDGAR es más precisa para uso intradía y viaja en los eventos de ingesta (D11).

## D5

### Verificación numérica: verificar, regenerar una vez y, si no, plantilla

- **Qué hice**: `src/copilot/verification.py` extrae cada cantidad de la respuesta (importes con escala, porcentajes, múltiplos, números), infiere su precisión de cómo está escrita ("416,2 mil millones de $" significa más o menos 0,05 mil millones) y solo la acepta si algún valor de la evidencia cae en ese intervalo. Años, fechas ISO, periodos fiscales, números de registro, tipos de formulario y marcadores de cita no son afirmaciones. En modo real, un borrador que falla se regenera una vez indicando en el *prompt* los números exactos sin respaldo; si vuelve a fallar, la respuesta se sustituye por la plantilla determinista construida con la misma evidencia y se marca `fallback_used`.
- **Por qué**: Pedir "usa solo los números proporcionados" reduce las alucinaciones pero no las elimina; una comprobación sí. La comparación según la precisión es lo que permite que "416,2 mil millones" pase y "416,3 mil millones" (un error del 0,03%) falle. Los números citados dentro de un pasaje recuperado del 10-K pasan a ser evidencia de ese pasaje: citar el informe está permitido, inventar una cifra no.
- **Encontrado al construirlo**: El verificador rechazó dos veces mi propia plantilla. "Kupiec test at the 5% level" afirmaba un nivel de significación que no constaba como evidencia (había pasado solo porque otro 0,05 coincidía), y la fórmula de crecimiento "revenue[FY2024] / revenue[FY2023] - 1" contenía una constante suelta. Ambos se arreglaron en la plantilla, no relajando el verificador.
- **Qué descarté**: Un LLM como juez (no determinista, cuesta una llamada y no puede ser un control de CI); la coincidencia exacta de cadenas (rechaza cualquier redondeo legítimo).
- **Qué supuse**: Que el signo no se comprueba en importes y porcentajes ("cayó un 3,1%" y "crecimiento del -3,1%" describen el mismo valor, y el VaR se expresa como pérdida positiva), y que pasarse de estricto (una regeneración) es la dirección de fallo correcta. Las fechas y los nombres aún no se verifican (README, limitaciones).

## D6

### Recuperación: BM25 + densa con Reciprocal Rank Fusion

- **Qué hice**: `src/copilot/retrieval_core.py` implementa Okapi BM25 sobre listas de *postings* (*stemming* ligero por sufijos, palabras vacías más las palabras propias de una pregunta) y lo combina con *embeddings* densos (OpenAI o Azure OpenAI) mediante Reciprocal Rank Fusion (k = 60) en modo real; el modo simulado usa solo BM25. Hay un índice por emisor y otro para todo el universo, de modo que una pregunta sobre NVIDIA solo busca en el 10-K de NVIDIA. Los extractos se centran en la consulta: empiezan en la frase que comparte más términos con ella.
- **Por qué**: Los *embeddings* densos captan paráfrasis; en un texto legal deciden términos raros y exactos ("talc", "Section 232", "export controls") que los *embeddings* diluyen. RRF combina posiciones, así que las dos puntuaciones nunca necesitan calibrarse entre sí. Con 833 pasajes, puntuar por fuerza bruta lleva menos de un milisegundo; un índice ANN añadiría una dependencia operativa sin ganancia medible.
- **Encontrado al construirlo**: La tasa de acierto de recuperación de la evaluación era del 78% con mi primer enfoque léxico. Dos causas: un *stemmer* incoherente ("regulation" quedaba en "regul" y "regulatory" en "regulat", así que nunca coincidían; "mention" quedaba en "ment") y palabras de pregunta como "does" con el IDF más alto de la consulta. Además, el pasaje correcto de JNJ quedaba primero, pero su frase relevante ("talc") estaba en el carácter 990, más allá del extracto de 600. Tras corregir el *stemmer*, la lista de palabras vacías y la selección del extracto: 100%. Reescribir BM25 sin scikit-learn ni SciPy quitó además unos 195 MB de la imagen.
- **Qué descarté**: Coseno TF-IDF (lo que usaba antes el modo simulado: débil con consultas cortas); una base de datos vectorial (por la escala); un *reranker* cross-encoder (una descarga de modelo para el modo sin red).
- **Qué supuse**: Que el Item 1A es el primer corpus adecuado para preguntas de riesgo; el MD&A sería la siguiente sección a indexar.

## D7

### El motor C++, y el benchmark que al principio decía que era más lento

- **Qué hice**: `cpp/riskcore` es una librería C++20 con *bindings* pybind11, compilada por scikit-build-core como miembro del *workspace* de uv, de modo que `uv sync` la compila. Ofrece VaR/ES histórico, paramétrico y Monte Carlo, un backtest móvil con el test de Kupiec, Cholesky y la inversa de la normal. Las entradas son vistas sin copia de los *buffers* de NumPy y el GIL se libera durante el cálculo. Una implementación de referencia en NumPy (`src/risk/reference.py`) es a la vez el oráculo de paridad (los estimadores deterministas coinciden con precisión de coma flotante) y la línea base del benchmark, escrita como NumPy vectorizado de calidad, no como un bucle.
- **Por qué**: Los controles de riesgo están en el camino de la petición del control pre-trade y deben ejecutarse en paralelo entre los hilos de la API; eso exige cálculo que libere el GIL y use todos los núcleos. El puesto pide Python y otro lenguaje orientado a objetos, y la frontera entre ambos (propiedad de la memoria a través del *binding*, GIL, determinismo) es donde de verdad fallan los sistemas híbridos.
- **Encontrado al construirlo**: El primer benchmark mostró el motor C++ **más lento** que NumPy en VaR histórico (21 ms frente a 4 ms) y en el backtest (0,5x). El perfilado dio tres causas: el `std::nth_element` de libstdc++ es unas dos veces más lento que el *introselect* de NumPy con esta entrada; los *bindings* copiaban cada entrada dos veces (16 MB de memoria nueva, con fallos de página, para 1 M de escenarios); y el backtest volvía a seleccionar cada ventana de 250 días desde cero. Correcciones: una selección por muestreo y filtrado para colas finas (un umbral sacado de una muestra espaciada de 32k con un margen de 4 sigmas, una pasada lineal y una selección entre unos 1.500 candidatos, con recurso a la selección completa para que el resultado sea siempre exacto); vistas `std::span` sin copia; y una ventana ordenada deslizante en el backtest. Resultado: 2-3x más rápido en VaR histórico, unas 8x en el backtest, unas 1,2x en Monte Carlo con un hilo y 7-8x con 8 hilos. Tests adversariales en C++ (entradas ordenadas, inversas, constantes y con muchos duplicados, de 1 M de valores) comprueban la vía rápida contra una ordenación completa.
- **Qué descarté**: Intrínsecos SIMD escritos a mano (`-O3` autovectoriza los bucles internos, y `-march=native` haría el *wheel* no portable); Numba (no demuestra un segundo lenguaje ni el diseño de un *binding*); publicar solo las cifras favorables.
- **Qué supuse**: Que la máquina del benchmark (un portátil de 4 núcleos con limitación térmica) da proporciones representativas pero no tiempos absolutos, por eso el README da rangos.

## D8

### Modelo Monte Carlo: horizonte de varios días, shocks t de Student, reproducible con cualquier número de hilos

- **Qué hice**: Las rentabilidades logarítmicas diarias se generan con una Normal o una t de Student multivariante (5 grados de libertad, reescalada para que su covarianza sea la muestral), se acumulan durante 10 días y el resultado es P&L = suma de w_i (exp(rentabilidad acumulada_i) - 1). Las trayectorias se generan en bloques fijos de 4.096, cada uno con su propio flujo xoshiro256** sembrado a partir de (semilla, índice de bloque); los hilos toman bloques de un contador atómico. Las normales usan el método polar de Marsaglia y la chi-cuadrado, el muestreo gamma de Marsaglia-Tsang.
- **Por qué**: Para una cartera lineal a un día bajo una distribución elíptica, el P&L de la cartera es univariante y el Monte Carlo solo reproduce una fórmula cerrada: implementarlo sería decorativo. Acumular 10 días hace el P&L no lineal y la suma de *shocks* t no es t, así que hace falta simular. La reproducibilidad es un requisito para una cifra de riesgo: el resultado es idéntico bit a bit con 1, 2, 3 y 8 hilos (probado). Se evitaron las distribuciones de la librería estándar porque `std::normal_distribution` difiere entre libstdc++ y libc++.
- **Encontrado al construirlo**: El primer muestreador usaba Box-Muller y una chi-cuadrado como suma de 5 normales al cuadrado: 15 normales por trayectoria y día, con llamadas trigonométricas. El método polar y el muestreo gamma lo bajaron a unas 11 y eliminaron la trigonometría.
- **Qué descarté**: Monte Carlo solo gaussiano (equivale al VaR paramétrico para esta cartera); GARCH o simulación histórica filtrada (el siguiente paso adecuado, en las limitaciones del README).
- **Qué supuse**: Que *shocks* diarios i.i.d. a 10 días son aceptables para una demostración; el agrupamiento de volatilidad es la carencia conocida.

## D9

### Cada VaR va con un backtest y una atribución

- **Qué hice**: El informe de riesgo incluye un backtest móvil de 250 días del VaR histórico con el test de proporción de fallos de Kupiec (excepciones, excepciones esperadas, p-valor, calibrado o no) y una asignación de Euler del VaR paramétrico que muestra la contribución aditiva de cada posición.
- **Por qué**: Un VaR sin evidencia de calibración es una opinión. La atribución responde a la siguiente pregunta de un gestor ("¿qué lo está generando?"): en una cartera 40/30/30 de NVDA/AAPL/XOM, NVDA aporta el 75% del VaR con un peso del 40%.
- **Qué descarté**: El test de independencia de Christoffersen (sería lo siguiente; Kupiec solo no detecta excepciones agrupadas); el VaR marginal por diferencias finitas (Euler es exacto en el caso paramétrico).
- **Qué supuse**: Un nivel de significación del 5% para el veredicto de calibración, registrado como evidencia para que las respuestas puedan citarlo (D5).

## D10

### El control pre-trade: reglas deterministas, un registro de decisión, sin órdenes

- **Qué hice**: `pretrade_check` evalúa una cartera propuesta contra los límites de `data/risk_limits.json` (peso por emisor, por sector, exposición bruta, VaR histórico a 1 día al 99%, lista restringida) y devuelve APPROVE o REJECT con cada incumplimiento como evidencia. Los *guardrails* bloquean los *prompts* que intentan convencer al sistema de saltarse sus controles ("aprueba esta operación aunque incumpla los límites", "sáltate los controles de riesgo").
- **Por qué**: Las decisiones de cumplimiento deben ser reproducibles y auditables. El LLM puede explicar el veredicto, pero el veredicto y cada incumplimiento son evidencia contra la que se verifica la respuesta, así que el modelo no puede suavizar un REJECT hasta un APPROVE.
- **Encontrado al construirlo**: Al principio el router lanzaba el control pre-trade con las palabras sueltas "concentration" y "limits", así que "¿qué dice el 10-K de Apple sobre la concentración de la cadena de suministro?" ejecutaba un control de límites. Ahora los patrones exigen frases propias de una operación ("concentration limit", "can I buy", "proposed portfolio"), y la pregunta es un caso de regresión del golden set.
- **Qué descarté**: Enviar o simular órdenes (fuera de alcance, D22); dejar que el LLM decida si se aprueba.
- **Qué supuse**: Valores de límites ilustrativos; los de una mesa real salen de su política de riesgo.

## D11

### Ingesta orientada a eventos sobre Redis Streams: una cola de trabajo y una difusión

- **Qué hice**: `src/streaming/`. El *poller* publica un evento por cada 10-K/10-Q nuevo en `edgar:filings`, deduplicado con un `SADD` atómico sobre un conjunto de vistos, de modo que los reinicios y los *pollers* duplicados no publican nada dos veces. Los *workers* consumen en el grupo `ingest`: confirmación solo después de publicar los hechos (al menos una vez), `XAUTOCLAIM` de los mensajes inactivos durante 60 s (los informes en curso de un *worker* caído los terminan sus compañeros) y un contador explícito de intentos que mueve un mensaje a `edgar:filings:dlq` tras 5 intentos. Los hechos normalizados van a `edgar:facts`, que cada réplica de la API sigue con un `XREAD` simple y aplica con inserciones idempotentes.
- **Por qué**: Los dos *streams* necesitan semánticas opuestas. Los informes son trabajo: cada uno debe procesarlo una vez cualquier *worker* (consumidores en competencia). Las actualizaciones de hechos son estado: cada réplica debe aplicarlas todas (difusión). Un grupo de consumidores en el segundo *stream* dejaría a cada réplica con una parte de las actualizaciones. Las inserciones idempotentes hacen segura la entrega al menos una vez.
- **Encontrado al construirlo**: La lógica de la cola de fallidos leía primero el número de entregas de `XPENDING`, que fakeredis no devuelve; poner un valor por defecto habría enviado el mensaje a la cola de fallidos al primer error. Un contador explícito con `HINCRBY` funciona igual en Redis, Valkey y el doble de pruebas.
- **Qué descarté**: Kafka (una dependencia operativa más pesada para decenas de informes al día; los grupos de consumidores, la repetición y la retención de Redis cubren este volumen, y Redis ya está en la pila para los límites de peticiones); el RSS de EDGAR (menos estructurado que los *submissions* por empresa).
- **Qué supuse**: Que un límite de 10.000 entradas por *stream* da suficiente historial para que una réplica que se reinicia se ponga al día; más allá de eso, las réplicas arrancan desde la instantánea del repositorio.

## D12

### Caché de respuestas con la versión de los datos en la clave, y una política de Redis que no puede perder la cola

- **Qué hice**: La clave de caché de `/ask` incluye la pregunta redactada, el contexto (tickers, `as_of`, cartera), el idioma, el modo, la versión de la app y el `data_version` del almacén de hechos. Redis funciona con desalojo `volatile-lru` y persistencia AOF.
- **Por qué**: Antes del *streaming*, los datos eran una instantánea estática y bastaba la versión de la app para invalidar la caché. Ahora un 10-Q puede llegar en cualquier momento, y una respuesta cacheada antes no debe servirse después. La política anterior, `allkeys-lru`, era correcta para un Redis solo de caché, pero bajo presión de memoria desalojaría sin avisar el *stream* de informes; `volatile-lru` solo desaloja claves con TTL (entradas de caché, contadores de límite).
- **Qué descarté**: Un Redis aparte para los *streams* (mejor aislamiento a escala, innecesario aquí, anotado para producción); caducidad solo por tiempo (sirve respuestas obsoletas hasta que vence el TTL).
- **Qué supuse**: Que ninguna herramienta aplica autorización por usuario, así que la clave no incluye al usuario; con datos por cliente, el cliente tendría que entrar antes en la clave.

## D13

### Orquestación: flujo de control con LangGraph, llamadas al LLM con Agno, un conjunto cerrado de herramientas

- **Qué hice**: LangGraph gestiona el estado y el flujo de control; las llamadas al LLM dentro de los nodos pasan por Agno. El router calcula una vez la lista ordenada de herramientas y cada nodo se quita a sí mismo del principio (una cola de trabajo acotada). Los nombres de herramientas son un conjunto `Literal` cerrado, el router en modo real solo puede añadir tickers del universo cubierto y ninguna herramienta ejecuta código ni SQL generado por el modelo.
- **Por qué**: Una cola acotada da un orden determinista y un número fijo de llamadas al LLM por petición (router, síntesis y como mucho un reintento) se activen las herramientas que se activen. La extracción de entidades es determinista en ambos modos, así que un ticker alucinado no puede llegar a una herramienta.
- **Qué descarté**: Un bucle ReAct (llamadas y elecciones de herramienta sin límite por petición); el *fan-out* en paralelo (conflictos de fusión sin necesidad de latencia, con herramientas de 2 a 20 ms cada una); un *checkpointer* (preguntas de un solo turno).
- **Qué supuse**: Que las preguntas de un solo turno cubren el caso de uso; las repreguntas necesitarían estado de conversación.

## D14

### Evaluación: verdad de referencia independiente, un test adversarial del verificador y umbrales obligatorios en CI

- **Qué hice**: `data/golden_set.json` tiene 37 casos (fundamentales, métricas derivadas, *point-in-time*, reexpresiones, carencias honestas, recuperación, riesgo, pre-trade, rutas mixtas). Las cifras esperadas se tomaron de *companyfacts* de la SEC por concepto y fecha de cierre con un script aparte, para que la evaluación no califique al normalizador consigo mismo. `scripts/evaluate_copilot.py` pasa cada caso por la app FastAPI real y después corrompe cada número de cada respuesta correcta (de -10% a +50%, y con dígitos transpuestos) y mide cuántas respuestas corrompidas rechaza el verificador. `scripts/check_eval_floors.py` hace fallar la CI por debajo de los umbrales.
- **Por qué**: Un golden set solo mide el camino feliz; el test adversarial mide directamente el mecanismo de seguridad (442 corrupciones, el 100% detectadas, 0 falsos positivos en respuestas correctas). Con el LLM simulado todo es determinista, así que cualquier caída es una regresión, no ruido.
- **Encontrado al construirlo**: La primera ejecución adversarial dio un 97,9% de *recall*. Todos los fallos eran errores del arnés, no del verificador: localizaba las afirmaciones con `str.find`, que encontraba el "10" dentro de la fecha "2026-10-01" en lugar de la afirmación, y corrompía la fecha. Ahora las afirmaciones llevan su posición exacta en el texto.
- **Qué descarté**: Respuestas calificadas por un LLM (no deterministas, no pueden ser un control de CI); generar los valores esperados desde el almacén de hechos (circular).
- **Qué supuse**: Que las métricas en modo simulado prueban el pipeline y el verificador, no el modelo; la ejecución con modelo real (`--real`) registra el tiempo hasta el primer token, los tokens y el coste por respuesta, pero necesita una clave de API.

## D15

### Seguridad de la API, guardrails y log de auditoría

- **Qué hice**: Validación de JWT Bearer (RS256 vía el JWKS del proveedor de identidad, o HS256 en local) con una lista explícita de algoritmos que nunca incluye `none`, `exp`/`sub` obligatorios y comprobación de audiencia, emisor y *scope*; el servicio se niega a arrancar con `APP_ENV=production` y la autenticación desactivada. La PII se redacta antes del router, del LLM, de la clave de caché, de los logs y del log de auditoría (las tarjetas deben pasar Luhn, se excluyen rangos de SSN que la SSA nunca emite, DNI/NIE, IBAN, email, teléfonos de 9 a 15 dígitos para que las fechas ISO sobrevivan). Los patrones de inyección de *prompt* en inglés y español, y los intentos de saltarse los controles pre-trade, devuelven 400. El límite de peticiones es una ventana fija por sujeto (o IP) en Redis, aplicada después de la autenticación para que un 401 nunca consuma cuota. Cada petición escribe una fila de auditoría (sujeto, resultado, ruta, latencia, recuentos de PII, ids de petición y de traza) con solo el SHA-256 de la pregunta redactada, nunca el texto.
- **Por qué**: Las reglas validadas son auditables y deterministas, lo que mantiene determinista el modo simulado de la CI; el verdadero límite de seguridad es arquitectónico (conjunto cerrado de herramientas, veredictos por reglas, cifras verificadas). Los límites de peticiones y la auditoría fallan en abierto, para que una caída de Redis o Postgres reduzca la protección en lugar de tumbar `/ask`.
- **Encontrado al construirlo**: (1) FastAPI descarta las `BackgroundTasks` cuando un endpoint lanza una excepción, lo que perdía justo las filas `blocked` y `error` que más interesan a un auditor; esos caminos ahora devuelven una respuesta con la tarea en segundo plano en lugar de lanzar. (2) `CREATE TABLE IF NOT EXISTS` no es atómico en Postgres: dos primeras escrituras concurrentes compitieron y una murió con una violación de unicidad en `pg_class`. Un *advisory lock* de transacción lo arregló; el test de regresión (4 réplicas x 8 escrituras concurrentes sobre una tabla nueva) falla 3 de 3 veces sin el arreglo.
- **Qué descarté**: *Guardrails* basados en LLM como NeMo Guardrails o Llama Guard (una llamada de modelo más por petición, no deterministas y sin tráfico real con el que medir sus falsos positivos); una escritura de auditoría síncrona que falla en cerrado (correcta para pagos, incorrecta para una herramienta analítica).
- **Qué supuse**: Que la validación del token pertenece al servicio aunque haya un API gateway delante (defensa en profundidad, y el sujeto validado es la clave del límite de peticiones y de la auditoría).

## D16

### Observabilidad: métricas, trazas, logs y alertas para un sistema con LLM

- **Qué hice**: Métricas de Prometheus para HTTP (RED por *handler* y código exacto), histogramas de latencia por nodo del grafo, herramientas invocadas, resultados de la verificación numérica (verificada, fallida, plantilla), tiempo hasta el primer token del LLM (medido en *streaming* de la llamada de síntesis), tokens y coste estimado, eventos de *guardrails*, aciertos de caché, tiempo de procesamiento y retraso de la ingesta, y la versión del almacén de hechos por réplica. *Spans* de OpenTelemetry por nodo bajo un *span* raíz por petición, exportados a través de un Collector a Jaeger y a Prometheus con *span metrics*. Logs JSON con ids de petición y de traza. Las alertas incluyen degradación de la verificación, tiempo hasta el primer token alto, informes en la cola de fallidos, retraso de ingesta y **divergencia entre réplicas** (réplicas de la API con distinta versión del almacén durante 10 minutos: una dejó de aplicar actualizaciones y sirve cifras obsoletas).
- **Por qué**: Para una función con LLM no basta con saber si está arriba: las preguntas operativas son si las respuestas siguen verificándose, cuánto espera el usuario el primer token y si todas las réplicas tienen los últimos informes. Histogramas y no *summaries*, porque los cuantiles solo se agregan entre réplicas a partir de los *buckets*.
- **Encontrado al construirlo**: El *span* SERVER del *middleware* termina después de que `/ask` haya sacado su traza del *buffer* en proceso, lo que habría recreado una fuga del *buffer*; el *buffer* ignora los *spans* SERVER, con un test de regresión.
- **Qué descarté**: El paquete de autoinstrumentación de FastAPI de OpenTelemetry (otra dependencia para unas 60 líneas que además necesitaban el filtro del *buffer*); un exportador gRPC (añade una dependencia nativa a la imagen sin ganancia a este volumen).
- **Qué supuse**: Que los precios de lista son una estimación aceptable del coste; la métrica se etiqueta como estimación y, si el proveedor devuelve el coste, se usa ese.

## D17

### Imagen de contenedor: una etapa de compilación C++ y tres trampas

- **Qué hice**: Un Dockerfile multietapa. La etapa de compilación instala g++ y compila riskcore con scikit-build-core (CMake y Ninja vienen de PyPI); la etapa final recibe solo el entorno virtual, el código y la instantánea de datos. Las imágenes base están fijadas por *tag* y *digest*, el proceso corre como UID 10001 con sistema de ficheros de solo lectura y sin *capabilities*, el *bytecode* está precompilado, el *health check* usa la librería estándar (sin curl) y se elimina el pip no usado de la imagen base.
- **Encontrado al construirlo**: (1) La primera imagen falló al importar: uv instala los miembros del *workspace* en modo *editable* por defecto, así que el entorno apuntaba a `/app/cpp/riskcore`, una ruta que solo existe en la etapa de compilación. `--no-editable` lo arregló, y `--no-install-project` evita una segunda copia del paquete `src` en *site-packages* que taparía la real. Lo detectó la prueba de humo de la CI, que ejecuta la imagen con las restricciones de Kubernetes. (2) La extensión compilada depende de libstdc++, que la imagen *slim* no garantiza; se enlaza de forma estática (`-static-libstdc++ -static-libgcc`). (3) Quitar scikit-learn y SciPy (D6) ahorró unos 195 MB; la memoria residente medida en caliente en el contenedor es de 226 MiB.
- **Qué descarté**: Incluir el compilador en la imagen final; publicar riskcore como *wheel* aparte en un registro (el paso adecuado cuando lo consuma un segundo servicio); *distroless* (el entorno virtual enlaza con el intérprete de la imagen base).
- **Qué supuse**: Despliegue en x86-64 (ambas etapas fijan `linux/amd64`, igual que la tarea de Fargate de D20).

## D18

### Kubernetes: la API y la ingesta como cargas separadas

- **Qué hice**: Base de Kustomize y un *overlay* local. El Deployment de la API mantiene la especificación endurecida (Pod Security restringido, sistema de ficheros de solo lectura, *capabilities* eliminadas, seccomp, sondas de arranque, *liveness* y *readiness*, retardo antes de parar, reparto por zona y nodo, HPA y PDB). La ingesta añade un Deployment de *workers* (2 réplicas, métricas en el puerto 9102) y un único *poller*, ambos con su propia NetworkPolicy que solo permite DNS, Redis y HTTPS hacia sec.gov. La *readiness* falla si el almacén de hechos está vacío.
- **Por qué**: Los *workers* son consumidores sin estado que compiten entre sí y escalan por su cuenta; atarlos a las réplicas de la API haría escalar la ingesta con el tráfico de consultas, que no tiene relación. El *overlay* local no tiene Redis, así que pone la ingesta a cero y la pila completa de *streaming* se ejecuta con docker-compose.
- **Qué descarté**: Helm (Kustomize cubre base y *overlays* sin plantillas); Redis como StatefulSet en el *namespace* (en producción es un servicio gestionado con persistencia y una política de desalojo *volatile*, documentado en el ejemplo de Secret).
- **Qué supuse**: Los CIDR de las NetworkPolicies y `FORWARDED_ALLOW_IPS` son valores de ejemplo que hay que ajustar a cada clúster.

## D19

### CI/CD y controles de calidad

- **Qué hice**: *Jobs* de GitHub Actions para lint (ruff, lockfile actualizado, la norma de estilo sin rayas largas), mypy (estricto en infraestructura, informes, riesgo, *streaming*, datos de mercado y el verificador), seguridad (Bandit sin hallazgos, escaneo de Trivy del repositorio), **C++** (compilación con avisos como errores `-Wall -Wextra -Wpedantic -Wconversion`, ctest en *release* y con AddressSanitizer + UndefinedBehaviorSanitizer), tests con un umbral del 85% de cobertura de ramas (94% medido), la evaluación y sus umbrales, un benchmark informativo en el resumen del *job*, tests de integración contra contenedores reales de Redis y Postgres (incluido el pipeline de *streams*), validación de la configuración de despliegue (kubeconform, promtool, otelcol, compose) y un *job* de contenedor (compilación, prueba de humo con las restricciones de Kubernetes que comprueba que la respuesta se verificó, escaneo de Trivy de la imagen). En `main`: publicación en GHCR con SBOM y procedencia SLSA, y firma *keyless* con cosign.
- **Por qué**: Cada control detecta una clase de fallo que los demás no ven: los *sanitizers* detectan accesos fuera de rango y comportamiento indefinido que los tests unitarios pasan por alto, la prueba de humo detectó el fallo del *editable install* (D17) y los umbrales de evaluación detectan regresiones en la calidad de las respuestas.
- **Qué descarté**: *Actions* de terceros que envuelven escáneres (los *tags* de aquasecurity/trivy-action se secuestraron en 2026 para robar secretos de CI; los escáneres se ejecutan como imágenes oficiales fijadas por *digest* y cada *action* está fijada a un SHA de commit); hacer del benchmark un control (los *runners* compartidos de CI son demasiado ruidosos para umbrales de tiempo).
- **Qué supuse**: Que la protección de rama exige el *job* `test`, cuyo identificador se mantiene estable por ese motivo.

## D20

### AWS ECS con Terraform

- **Qué hice**: `terraform/` despliega la API en ECS Fargate detrás de un ALB en una VPC mínima (dos subredes públicas, sin NAT gateway), con un repositorio ECR, logs de CloudWatch con retención explícita, un rol de ejecución sin rol de tarea (la app no llama a ninguna API de AWS) y un secreto opcional de Secrets Manager para la clave del LLM. Está validado (`fmt`, `init`, `validate`) pero no aplicado; nada cuesta dinero hasta `terraform apply`.
- **Por qué**: Muestra la misma imagen en un servicio de contenedores gestionado con IAM de mínimo privilegio, y el coste está acotado (unos 35-58 $ al mes si se deja encendido, sobre todo el ALB y Fargate).
- **Qué descarté**: Un NAT gateway (unos 33 $ al mes por sí solo, evitado con subredes públicas y grupos de seguridad estrictos); EKS para un único servicio (los manifiestos de Kubernetes cubren ese camino).
- **Qué supuse**: Un despliegue a escala de demostración: una tarea y estado local. Un entorno compartido necesita un *backend* remoto con bloqueo y los *workers* de ingesta como servicios ECS adicionales.

## D21

### Mantenimiento de dependencias: dos pull requests en rojo y un main en rojo

- **Qué hice**: Arreglé la CI rota de `main` y de dos *pull requests* de Dependabot. `main` fallaba en los dos controles de Trivy por CVE HIGH en dependencias transitivas (urllib3 2.7.0 y virtualenv 21.7.3), corregidas actualizándolas en el *lockfile*. El PR #60 (uvicorn 0.54.0) fallaba en `uv lock --check` porque Dependabot editó la entrada del paquete pero no los requisitos registrados del proyecto; se sustituyó por un *relock* correcto. El PR #57 (imagen base con Python 3.14) fallaba porque `requires-python` es `<3.14`, así que la imagen no tenía un intérprete compatible; Dependabot ignora ahora los saltos de versión menor y mayor de Python en la imagen base, mientras que los parches y las actualizaciones de *digest* siguen llegando.
- **Por qué**: Una versión menor de Python es una migración del entorno de ejecución (*wheels*, extensiones en C, el motor compilado), no una actualización rutinaria, y debe ser un *pull request* deliberado.
- **Qué descarté**: Fusionar el PR #57 ampliando `requires-python` sin probar la pila en 3.14.
- **Qué supuse**: Que los servicios de la CI (Redis, Postgres) deben seguir las versiones de docker-compose; se alinearon (8.10 y 18.6).

## D22

### Lo que no se construye a propósito

- **Sin enrutado ni ejecución de órdenes.** El control pre-trade devuelve un registro de decisión.
- **Sin datos de mercado en tiempo real.** Los precios son una instantánea diaria de un endpoint público para demostración; el proveedor está detrás de una función (`src/marketdata/prices.py`) para que lo sustituya uno con licencia.
- **Sin afirmaciones sobre la latencia de servir modelos** (vLLM, TensorRT-LLM). El presupuesto de latencia del sistema lo domina la llamada al LLM alojado, que se mide (tiempo hasta el primer token) en lugar de suponerse.
- **Sin opciones ni instrumentos no lineales, y sin agrupamiento de volatilidad** en el modelo de riesgo.
- **Sin verificación de fechas ni nombres**, solo de cantidades (D5).

Cada uno es una decisión de alcance con un siguiente paso explícito, no un descuido.

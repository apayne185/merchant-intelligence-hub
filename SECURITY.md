# Security

## Data

Every dataset in this repo is **synthetic** — `data/transactions_sample.csv`,
`data/copilot_fixture_transactions.csv`, `data/merchants_context.json`,
`data/historical_complaints.json`, `data/policy_docs.json`, and
`data/golden_set*.json`. None of it is real merchant, cardholder, or PII
data. Quality issues in the transaction data (mixed date formats, BR-locale
decimals, duplicates, leakage traps) were planted intentionally for the
original exercise — see `DECISIONS.md`.

## Model files

`outputs/model.pkl` is a `joblib`-pickled sklearn `Pipeline`. Pickle
deserialization executes arbitrary code on load. Only run `joblib.load()`
against a `model.pkl` you built yourself from this repo (or otherwise trust
the provenance of) — never against one downloaded from an untrusted source.
See `outputs/model_card.md` and `DECISIONS.md` D24 for the same warning in
context, including the model's own weak-discrimination limitations.

Models retrained through MLflow (`scripts/train_churn_mlflow.py`, D57) are
saved in **skops** format instead of pickle: loading doesn't execute
arbitrary code, and only the explicitly trusted types listed in
`src/mlops/churn_training.py:SKOPS_TRUSTED_TYPES` are allowed.

## Copilot API access control (D52, D53)

- **Authentication**: `AUTH_MODE=jwt` validates Bearer JWTs, either RS256
  against an identity provider's JWKS (`AUTH_JWKS_URL`) or HS256 with a shared
  secret (local/dev only). Algorithms come from an explicit allowlist (never
  `none`), `exp`/`sub` are required, `aud`/`iss` are enforced when configured,
  and the `copilot:ask` scope is required. The service **refuses to start**
  with `APP_ENV=production` and `AUTH_MODE=none`.
- **Rate limiting**: per JWT subject (or client IP if anonymous), shared
  across replicas via Redis. It **fails open** if Redis is unavailable: a
  deliberate availability choice, logged and counted in
  `copilot_guardrail_events_total{event="ratelimit_backend_error"}`. The k8s
  Ingress adds a coarse per-IP edge limit in front of it.
- **`/metrics`, `/health`, `/ready`** are unauthenticated by design for
  in-cluster scrapers and probes. The Ingress blocks `/metrics` externally.

## PII and prompt injection / LLM guardrails (D52)

- **PII redaction** (`src/copilot/infra/guardrails.py`) runs on every `/ask`
  question *before* it reaches the router, the synthesis LLM, the response
  cache key, logs, or the audit table. It covers Luhn-validated card numbers,
  US SSNs, Brazilian CPFs, IBANs, emails and phone numbers. The API returns
  the redacted question and never echoes the original back. The audit log
  stores only a SHA-256 of the redacted question, never its text.
- **Prompt injection**: `src/copilot/infra/guardrails.py` (which extends
  `src/parte4_api/agent.py:detect_prompt_injection`) blocks matching requests
  with HTTP 400. It remains **best-effort pattern matching**, not a hard
  security boundary. It catches the injection patterns in
  `data/golden_set*.json` and similar phrasing, not every possible prompt
  injection technique. The real boundary is architectural: the router can
  only emit a closed set of tool names, and no tool executes LLM-generated
  SQL or code. Don't rely on these pattern guardrails alone if adapting this
  code for a system that handles real user input against a real LLM.

## Container and supply chain (D56)

- The image runs as non-root UID 10001, with a read-only root filesystem
  and all capabilities dropped (enforced by the k8s Pod Security `restricted`
  profile). Base images are pinned by digest.
- CI blocks on bandit (SAST), a Trivy dependency/secret scan of the repo,
  and a Trivy scan of the built image (fixable HIGH/CRITICAL).
- Published images carry an SBOM and SLSA provenance and are signed with
  cosign (keyless, GitHub OIDC). Verify with:
  `cosign verify ghcr.io/apayne185/merchant-intelligence-hub@<digest> --certificate-identity-regexp 'https://github.com/apayne185/merchant-intelligence-hub/' --certificate-oidc-issuer https://token.actions.githubusercontent.com`

## Secrets

`.env`, `.env.local`, `*.key`, and `*.pem` are gitignored. Never commit an
`OPENAI_API_KEY` or any other credential. `.pre-commit-config.yaml` runs
[gitleaks](https://github.com/gitleaks/gitleaks) locally before commit as a
backstop, not a guarantee — review `git diff` before pushing regardless.

## SQL execution

The Data Analyst tool (`src/copilot/tools/data_analyst.py`) only ever binds
typed, validated arguments into a small, fixed set of hand-written
parameterized DuckDB query templates — it never executes LLM-generated or
user-supplied SQL text directly. See `DECISIONS.md` D23 for the reasoning
(an LLM-writes-SQL design would be a real injection/exfiltration risk
class this avoids entirely).

## Reporting an issue

This is a portfolio project, not a production system with an active
security team. If you find something concerning, please open a GitHub
issue rather than a public PR with exploit details.

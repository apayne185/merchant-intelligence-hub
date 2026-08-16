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

## Prompt injection / LLM guardrails

`src/parte4_api/agent.py:detect_prompt_injection` and the copilot router's
keyword rules are **best-effort pattern matching**, not a hard security
boundary. They catch the injection patterns in `data/golden_set*.json` and
similar phrasing, not every possible prompt injection technique. Don't rely
on this repo's guardrails alone if adapting this code for a system that
handles real user input against a real LLM.

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

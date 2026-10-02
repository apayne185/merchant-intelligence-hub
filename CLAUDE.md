# Repository notes for AI assistants and contributors

## House rules

- **Never use the em dash character (U+2014)** anywhere in this repository: code, comments, docs, Spanish text and commit messages included. Use a colon, comma, parentheses or a plain hyphen. `scripts/check_no_em_dash.py` enforces it in pre-commit and CI.
- **Documentation is bilingual**: the full English text first, then a complete Spanish replica below it (not a summary and not links back to the English). Code comments and docstrings are English only.
- Keep the repository focused on the Filings & Risk Copilot; do not add references to unrelated earlier projects.
- Record design decisions in `DECISIONS.md` in its four-question format (what I did, why, what I rejected, what I assumed), adding "found while building" when testing exposed a bug. Add each new decision in both languages.

## Working on the code

- `uv sync --extra dev` compiles the C++ engine (`cpp/riskcore`); `make check` runs what CI runs; `make eval` must stay at its floors (`scripts/check_eval_floors.py`).
- Every number an answer states must be backed by an `Evidence` value produced by a tool; if a template or prompt change adds a number (a constant, a parameter), add it to the evidence rather than loosening `src/copilot/verification.py`.
- Golden-set expected figures come from SEC companyfacts directly, never from the fact store under test.
- `SEC_USER_AGENT` is read from the environment and never committed.

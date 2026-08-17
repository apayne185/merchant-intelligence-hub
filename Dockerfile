# Container image for the Merchant Intelligence Copilot API (src/copilot/api.py).
# Built for the AWS/ECS Fargate deployment in terraform/ — see terraform/README.md
# for the apply/destroy runbook and DECISIONS.md D33 for the design rationale,
# including two failure modes this Dockerfile exists specifically to avoid
# (uvicorn's default host binding, and LightGBM's libgomp1 dependency).
#
# Build (match the Fargate task's architecture explicitly rather than relying
# on defaults agreeing — see D33):
#     docker buildx build --platform linux/amd64 -t merchant-copilot:latest .
#
# Run locally (MOCK_LLM=1: zero cost, no OPENAI_API_KEY needed):
#     docker run --rm -p 8001:8001 -e MOCK_LLM=1 merchant-copilot:latest
#     curl localhost:8001/health

# --platform=linux/amd64 pinned explicitly on both stages, not just
# documented via the `buildx --platform` build command above — a plain
# `docker build` (skipping that flag, e.g. on an arm64 dev machine) would
# otherwise silently produce an arm64 image that pushes and applies
# cleanly, then fails only when Fargate tries to run it against
# terraform/ecs.tf's runtime_platform{cpu_architecture="X86_64"}. See
# DECISIONS.md D33.
FROM --platform=linux/amd64 python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# uv's default hardlink-from-cache behavior doesn't survive being copied
# into a separate final stage below (Astral's own Docker guidance).
ENV UV_LINK_MODE=copy

# Dependency layer first (cacheable independently of src/ changes). Plain
# `uv sync --frozen` with no --extra flags already installs only base
# `dependencies` — dev/bonus/pyspark are opt-in extras, not auto-installed.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project

# Now install the project itself.
COPY src/ ./src/
RUN uv sync --frozen


FROM --platform=linux/amd64 python:3.13-slim AS runtime

# LightGBM's Linux wheel (outputs/model.pkl, loaded by
# src/copilot/tools/risk.py) dynamically links libgomp.so.1, which isn't on
# slim base images. Without this, the container builds and /health passes
# fine (it never touches the model) — the first risk-routed /ask throws
# OSError at request time instead. See DECISIONS.md D33.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
ENV PATH="/app/.venv/bin:$PATH"

# Only the specific outputs/ files src/copilot/tools actually read at
# runtime — not `outputs/` wholesale, which would drag in outputs/delta/
# and monthly_kpis*.csv (~23MB of unused PySpark side-output).
COPY outputs/model.pkl outputs/feature_importance.csv ./outputs/
COPY data/policy_docs.json data/historical_complaints.json \
     data/merchants_context.json data/copilot_fixture_transactions.csv ./data/
# data/transactions_sample.csv (the real ~200k-row dataset) is gitignored
# and never in a checkout to begin with — src/copilot/tools/data_analyst.py:
# default_csv_path() already falls back to the fixture above automatically.

EXPOSE 8001

# --host 0.0.0.0 is required: every documented run command elsewhere in
# this repo omits it, and uvicorn's CLI default is 127.0.0.1 — inside a
# container that means unreachable from outside the container's own network
# namespace, while `docker exec <container> curl localhost:8001` would
# falsely succeed (same namespace). See DECISIONS.md D33.
CMD ["uvicorn", "src.copilot.api:app", "--host", "0.0.0.0", "--port", "8001"]

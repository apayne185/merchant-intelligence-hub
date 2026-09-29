# syntax=docker/dockerfile:1.7
# Container image for the Merchant Intelligence Copilot API (src/copilot/api.py).
# Runs on AWS/ECS Fargate (terraform/), Kubernetes (k8s/) and docker-compose
# (docker-compose.yml). See DECISIONS.md D33 (original design, including two
# failure modes this file exists to avoid: uvicorn's default host binding,
# LightGBM's libgomp1 dependency) and D56 (hardening: digest pins, non-root,
# read-only-rootfs compatible, healthcheck, Trivy-gated in CI).
#
# Build (match the Fargate task's architecture explicitly rather than relying
# on defaults agreeing — see D33):
#     docker buildx build --platform linux/amd64 -t merchant-copilot:latest .
#
# Run locally (MOCK_LLM=1: zero cost, no OPENAI_API_KEY needed):
#     docker run --rm -p 8001:8001 -e MOCK_LLM=1 --read-only --tmpfs /tmp merchant-copilot:latest
#     curl localhost:8001/health

# Base images pinned by tag *and* digest: the tag documents intent, the
# digest makes the build reproducible and immune to a re-pushed tag. Bumped
# deliberately via PRs from dependabot's docker ecosystem
# (.github/dependabot.yml), not silently on every build the way `uv:latest`
# used to be. Literal FROM lines rather than ARG defaults on purpose:
# dependabot only rewrites image references it can see in FROM.
FROM ghcr.io/astral-sh/uv:0.11.17@sha256:03bdc89bb9798628846e60c3a9ad19006c8c3c724ccd2985a33145c039a0577b AS uv

# --platform=linux/amd64 pinned explicitly on both stages, not just
# documented via the `buildx --platform` build command above — a plain
# `docker build` (skipping that flag, e.g. on an arm64 dev machine) would
# otherwise silently produce an arm64 image that pushes and applies
# cleanly, then fails only when Fargate tries to run it against
# terraform/ecs.tf's runtime_platform{cpu_architecture="X86_64"}. See
# DECISIONS.md D33.
FROM --platform=linux/amd64 python:3.14-slim-trixie@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d AS builder

COPY --from=uv /uv /uvx /bin/

WORKDIR /app

# UV_LINK_MODE=copy: uv's default hardlink-from-cache doesn't survive being
# copied into the final stage (Astral's own Docker guidance).
# UV_COMPILE_BYTECODE=1: .pyc compiled at build time — faster cold start,
# and required anyway under a read-only root filesystem (k8s), where
# Python can't write __pycache__ at runtime.
# UV_PYTHON_DOWNLOADS=never: use the base image's interpreter, never fetch one.
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

# Dependency layer first (cacheable independently of src/ changes). Plain
# `uv sync --frozen` with no --extra flags installs only base
# `dependencies` — dev/mlops/bonus/pyspark are opt-in extras, never in the
# image. The BuildKit cache mount keeps uv's download cache across builds
# without it ending up in any layer.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

# Now install the project itself.
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen


# Must stay the same image as the builder stage: the venv's interpreter
# symlinks point at this base's /usr/local/bin/python3.13.
FROM --platform=linux/amd64 python:3.14-slim-trixie@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d AS runtime

ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="merchant-intelligence-copilot" \
      org.opencontainers.image.description="Multi-agent merchant-intelligence API (FastAPI + LangGraph)" \
      org.opencontainers.image.source="https://github.com/apayne185/merchant-intelligence-hub" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"

# LightGBM's Linux wheel (outputs/model.pkl, loaded by
# src/copilot/tools/risk.py) dynamically links libgomp.so.1, which isn't on
# slim base images. Without this, the container builds and /health passes
# fine (it never touches the model) — the first risk-routed /ask throws
# OSError at request time instead. See DECISIONS.md D33.
# `apt-get upgrade` picks up Debian security fixes published after the base
# digest was cut — the difference between a clean and a failing Trivy gate.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    # The base image's system pip is never used (the app runs from the
    # uv-built venv, which has no pip) but its vendored msgpack and
    # pkg_resources carried fixable HIGH CVEs that failed the Trivy gate.
    # Not needed at runtime -> not shipped. See DECISIONS.md D56.
    && python -m pip uninstall -y pip

# Fixed, non-root UID/GID (10001): k8s `runAsNonRoot` can verify a numeric
# USER, and a UID above the host's normal range can't collide with a real
# host account if a volume is ever shared.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app

# Everything is copied root-owned and left non-writable for UID 10001: the
# process can read its code, venv and model but can't modify them — a
# compromised process can't persist by rewriting its own source.
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src

# Only the specific outputs/ files src/copilot/tools actually read at
# runtime — not `outputs/` wholesale, which would drag in outputs/delta/
# and monthly_kpis*.csv (~23MB of unused PySpark side-output).
COPY outputs/model.pkl outputs/feature_importance.csv ./outputs/
COPY data/policy_docs.json data/historical_complaints.json \
     data/merchants_context.json data/copilot_fixture_transactions.csv ./data/
# data/transactions_sample.csv (the real ~200k-row dataset) is gitignored
# and never in a checkout to begin with — src/copilot/tools/data_analyst.py:
# default_csv_path() already falls back to the fixture above automatically.

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LOG_FORMAT=json \
    APP_VERSION=${VERSION}

USER 10001:10001

EXPOSE 8001

# Docker/compose-level liveness. No curl in slim images, and installing it
# just for this would add attack surface Trivy then has to scan — the
# stdlib does the same job. (k8s ignores HEALTHCHECK; it uses the probes in
# k8s/base/deployment.yaml against /health and /ready instead.)
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=2).status == 200 else 1)"]

# --host 0.0.0.0 is required: every documented run command elsewhere in
# this repo omits it, and uvicorn's CLI default is 127.0.0.1 — inside a
# container that means unreachable from outside the container's own network
# namespace, while `docker exec <container> curl localhost:8001` would
# falsely succeed (same namespace). See DECISIONS.md D33.
# --proxy-headers: trust X-Forwarded-For only from FORWARDED_ALLOW_IPS
# (uvicorn reads that env var; set to the ingress/ALB range in k8s) so the
# per-IP rate limit sees the real client, not the load balancer.
# --no-access-log: the request middleware writes a richer structured
# access line (request_id, trace_id, duration) — uvicorn's would duplicate it.
# One worker per container on purpose: scale via replicas/HPA, and the
# Prometheus registry stays single-process (src/copilot/infra/metrics.py).
CMD ["uvicorn", "src.copilot.api:app", "--host", "0.0.0.0", "--port", "8001", \
     "--proxy-headers", "--no-access-log", "--timeout-graceful-shutdown", "20"]

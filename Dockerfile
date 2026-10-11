# syntax=docker/dockerfile:1.7
# Container image for the Filings & Risk Copilot. One image, three processes:
#   API:     uvicorn src.copilot.api:app            (default CMD)
#   worker:  python -m src.streaming.worker          (EDGAR ingest consumer group)
#   poller:  python -m src.streaming.poller --live   (new-filing detector)
#
# Builder compiles the C++ engine (cpp/riskcore) with a full toolchain; the
# runtime stage gets only the built venv, so no compiler ships. libstdc++ is
# linked statically into the extension, so the slim runtime needs no extra
# system packages.
#
#     docker buildx build --platform linux/amd64 -t filings-risk-copilot:latest .
#     docker run --rm -p 8001:8001 -e MOCK_LLM=1 --read-only --tmpfs /tmp filings-risk-copilot:latest

# Base images pinned by tag and digest: the tag documents intent, the digest
# makes builds reproducible. Dependabot moves both (python minor bumps are a
# deliberate migration and are excluded there).
FROM ghcr.io/astral-sh/uv:0.13.0@sha256:cdc6093146eb3ff6a40107b38f008b789e050e77ad87865e381d9917da55a168 AS uv

# --platform pinned on both stages: a plain `docker build` on an arm64 laptop
# would otherwise produce an image that only fails once Fargate (X86_64 in
# terraform/ecs.tf) tries to run it.
FROM --platform=linux/amd64 python:3.13-slim-trixie@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b AS builder

COPY --from=uv /uv /uvx /bin/

# C++ toolchain for riskcore (builder only). CMake and Ninja come from PyPI as
# scikit-build-core build requirements, pinned in cpp/riskcore/pyproject.toml.
RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# copy: hardlinks from uv's cache do not survive the copy into the runtime stage.
# bytecode: compiled at build time (required under a read-only root filesystem).
# no downloads: use the base image's interpreter.
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

# Dependencies, including the riskcore workspace member compiled from source.
# --no-editable: uv installs workspace members editable by default, which
# would leave the venv pointing at /app/cpp/riskcore, a path that only exists
# in this stage. --no-install-project: the app runs from /app/src; installing
# the root project too would put a second `src` package in site-packages.
COPY pyproject.toml uv.lock README.md ./
COPY cpp/riskcore ./cpp/riskcore
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-editable --no-install-project

COPY src/ ./src/


# Must stay the same image as the builder stage: the venv's interpreter
# symlinks point at this base's /usr/local/bin/python3.13.
FROM --platform=linux/amd64 python:3.13-slim-trixie@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b AS runtime

ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="filings-risk-copilot" \
      org.opencontainers.image.description="Multi-agent copilot over SEC filings and market risk (FastAPI, LangGraph, C++ riskcore)" \
      org.opencontainers.image.source="https://github.com/apayne185/merchant-intelligence-hub" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"

# `apt-get upgrade` picks up Debian security fixes published after the base
# digest was cut. The base image's own pip is never used (the app runs from
# the uv-built venv) and has carried fixable CVEs, so it is removed.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall -y pip

# Fixed non-root UID/GID: k8s runAsNonRoot can verify a numeric USER.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app

# Root-owned and read-only for UID 10001: a compromised process cannot
# persist by rewriting its own code or data.
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
# Real SEC/price snapshot the fact store and risk engine boot from; the
# streaming worker keeps the in-memory copy current after startup.
COPY data/universe.json data/risk_limits.json ./data/
COPY data/xbrl ./data/xbrl
COPY data/filings ./data/filings
COPY data/prices ./data/prices

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LOG_FORMAT=json \
    APP_VERSION=${VERSION}

USER 10001:10001

EXPOSE 8001

# Stdlib health probe: no curl in the image, so nothing extra for Trivy to scan.
# (k8s uses its own probes against /health and /ready.)
HEALTHCHECK --interval=15s --timeout=3s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=2).status == 200 else 1)"]

# --host 0.0.0.0: uvicorn's default 127.0.0.1 is unreachable from outside the
# container. --proxy-headers trusts X-Forwarded-For only from
# FORWARDED_ALLOW_IPS, so per-IP rate limits see the real client. One worker
# per container: scale with replicas, keep the Prometheus registry single-process.
CMD ["uvicorn", "src.copilot.api:app", "--host", "0.0.0.0", "--port", "8001", \
     "--proxy-headers", "--no-access-log", "--timeout-graceful-shutdown", "20"]

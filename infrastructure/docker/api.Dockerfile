# syntax=docker/dockerfile:1
# FastAPI backend image — pyproject-driven (PEP 621).
#
# Installs the [api] extra of the fxvol project — fastapi + uvicorn +
# slowapi + prometheus + httpx, on top of the base runtime (numpy /
# scipy / pandas / redis / structlog / pydantic / SQLAlchemy / asyncpg
# / alembic). No ib_insync, no arch — the api is pure stateless.
#
#   docker build -f infrastructure/docker/api.Dockerfile -t fx-options-api .

# Base images pinned by tag AND digest (#305, audit 10#6); Dependabot's
# docker ecosystem refreshes both together. uv installs from uv.lock (#303).
ARG PYTHON_IMAGE=python:3.11.18-slim-bookworm@sha256:a83ba18aef0f865b1a4a611c2cf460fe446f2795673bbb9fe43be570439e3b39
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490

FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE} AS base
COPY --from=uv /uv /bin/uv
# The app lives in the uv venv; the base image's pip / setuptools / wheel are
# never used and Trivy flags them (pip 24.0, wheel 0.45, jaraco.context 5.3).
RUN python -m pip uninstall -y pip setuptools wheel

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH=/app/.venv/bin:$PATH

# Build deps required by asyncpg / scipy wheels on slim.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, from the lockfile only: this layer is cached until
# pyproject.toml / uv.lock change; --locked refuses a stale lock, so the
# image runs exactly the set CI tested (#303, audit 10#5).
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project --no-editable --extra api
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --extra api

COPY scripts/ ./scripts/

# Build identity surfaced by GET /api/v1/health (#344): the deploy smoke
# asserts the sha it just shipped is the one answering. build.yml passes it;
# a local build reports "unknown".
ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA}

# Non-root runtime (#298, audit 01#10): the image installs as root, then drops
# to an unprivileged user. The service listens above 1024 and writes nothing
# (PYTHONDONTWRITEBYTECODE, logs to stdout), so no directory needs to be writable.
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin fxvol \
    && chown -R fxvol:fxvol /app
USER fxvol

EXPOSE 8000

# Readiness, not liveness: /health/ready probes Postgres and Redis and answers
# 503 when either is down, so nginx's depends_on and the orchestrator see the
# api's real state (#344, OPS-12). /health stays the constant liveness probe.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/v1/health/ready || exit 1

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

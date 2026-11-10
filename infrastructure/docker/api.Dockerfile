# syntax=docker/dockerfile:1
# FastAPI backend image — pyproject-driven (PEP 621).
#
# Installs the [api] extra of the fxvol project — fastapi + uvicorn +
# slowapi + prometheus + httpx, on top of the base runtime (numpy /
# scipy / pandas / redis / structlog / pydantic / SQLAlchemy / asyncpg
# / alembic). No ib_insync, no arch — the api is pure stateless.
#
#   docker build -f infrastructure/docker/api.Dockerfile -t fx-options-api .

ARG PYTHON_IMAGE=python:3.11-slim

FROM ${PYTHON_IMAGE} AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src

# Build deps required by asyncpg / scipy wheels on slim.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# pyproject.toml + src/ are needed together because PEP 517 build
# resolves the package layout from src/ when installing fxvol[api].
# Cache breaks on any src/ change ; if dep iteration becomes a concern,
# split into a deps-only first stage with a stub src/ tree.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/pip pip install --upgrade pip && pip install ".[api]"

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

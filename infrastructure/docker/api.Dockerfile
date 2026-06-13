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

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/v1/health || exit 1

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

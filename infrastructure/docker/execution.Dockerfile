# Execution engine — IB-connected microservice owning the order /
# trade / position lifecycle. The api forwards REST user requests to
# this service via internal HTTP.
#
# Installs the [api,ib] extras : uvicorn (HTTP server on :8001) +
# ib-insync + tzdata.
#
#   docker build -f infrastructure/docker/execution.Dockerfile -t fx-options-execution .

ARG PYTHON_IMAGE=python:3.11-slim

FROM ${PYTHON_IMAGE} AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --upgrade pip && pip install ".[api,ib]"

EXPOSE 8001

HEALTHCHECK --interval=15s --timeout=3s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8001/health || exit 1

CMD ["uvicorn", "engines.execution.main:app", "--host", "0.0.0.0", "--port", "8001", "--proxy-headers"]

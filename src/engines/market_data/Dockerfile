# syntax=docker/dockerfile:1
# Market-data service — async IB tick publisher.
#
# Installs the [ib] extra (ib-insync + tzdata + base runtime). Multi-
# stage pattern shared with risk-engine ; only the CMD differs.

ARG PYTHON_IMAGE=python:3.11-slim

FROM ${PYTHON_IMAGE} AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# Minimal apt — ib_insync is pure Python, numpy/scipy wheels exist for
# manylinux so build-essential is not needed at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/pip pip install --upgrade pip && pip install ".[ib]"

RUN useradd -m -u 1001 fxvol && chown -R fxvol:fxvol /app
USER fxvol

# Healthcheck : TTL of the Redis heartbeat must be < 60s.
# Same check as docker-compose.yml (one implementation, #344): ISO-8601
# heartbeat younger than 60 s AND, during FX market hours, a live EURUSD tick
# (latest_spot key present, written within 120 s). The previous inline
# float(ts) could only fail.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -m shared.healthcheck heartbeat market_data 60 --tick EURUSD 120

CMD ["python", "-m", "engines.market_data.main"]

# syntax=docker/dockerfile:1
# Risk-engine service — installs the [ib] extra (ib-insync + base
# runtime, no GARCH).

ARG PYTHON_IMAGE=python:3.11-slim

FROM ${PYTHON_IMAGE} AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/pip pip install --upgrade pip && pip install ".[ib]"

RUN useradd -m -u 1003 fxvol && chown -R fxvol:fxvol /app
USER fxvol

# 2s cycle cadence — heartbeat TTL 30s tolerates one missed cycle.
# Same check as docker-compose.yml (one implementation, #344): ISO-8601
# heartbeat younger than 30 s. The previous inline float(ts) could only fail.
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD python -m shared.healthcheck heartbeat risk_engine 30

CMD ["python", "-m", "engines.risk.main"]

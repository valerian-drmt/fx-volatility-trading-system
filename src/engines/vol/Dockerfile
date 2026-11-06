# syntax=docker/dockerfile:1
# Vol-engine service — installs the [quant] extra (transitively pulls
# [ib]) for GARCH (arch) + GMM regime detection (scikit-learn) on top
# of the IB stack.

ARG PYTHON_IMAGE=python:3.11-slim

FROM ${PYTHON_IMAGE} AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# arch ships manylinux wheels for 3.11 ; build-essential is a belt.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/pip pip install --upgrade pip && pip install ".[quant]"

# Backfill scripts (one-shot data seeding, e.g. backfill_iv_history_for_gmm.py).
COPY scripts/ ./scripts/

RUN useradd -m -u 1002 fxvol && chown -R fxvol:fxvol /app
USER fxvol

# Same check as docker-compose.yml (one implementation, #344): ISO-8601
# heartbeat younger than 300 s. The previous inline float(ts) could only fail.
HEALTHCHECK --interval=60s --timeout=5s --start-period=240s --retries=3 \
    CMD python -m shared.healthcheck heartbeat vol_engine 300

CMD ["python", "-m", "engines.vol.main"]

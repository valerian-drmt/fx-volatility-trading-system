# syntax=docker/dockerfile:1
# Analytics-engine service — installs the [quant] extra (scikit-learn for the
# GMM regime refit + numpy). No IB : this engine reads Redis/Postgres only.

ARG PYTHON_IMAGE=python:3.11-slim

FROM ${PYTHON_IMAGE} AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# scikit-learn ships manylinux wheels for 3.11 ; build-essential is a belt.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/pip pip install --upgrade pip && pip install ".[quant]"

RUN useradd -m -u 1006 fxvol && chown -R fxvol:fxvol /app
USER fxvol

# Same check as docker-compose.yml (one implementation, #344): ISO-8601
# heartbeat younger than 300 s. The previous inline float(ts) could only fail.
HEALTHCHECK --interval=60s --timeout=5s --start-period=240s --retries=3 \
    CMD python -m shared.healthcheck heartbeat analytics_engine 300

CMD ["python", "-m", "engines.analytics.main"]

# syntax=docker/dockerfile:1
# db-writer service — installs the [writer] extra (psycopg2-binary on
# top of base runtime). Deliberately does NOT pull ib-insync, making
# this image ~150 MB lighter than the engine containers.

ARG PYTHON_IMAGE=python:3.11-slim

FROM ${PYTHON_IMAGE} AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/pip pip install --upgrade pip && pip install ".[writer]"

RUN useradd -m -u 1004 fxvol && chown -R fxvol:fxvol /app
USER fxvol

# Same check as docker-compose.yml (one implementation, #344): ISO-8601
# heartbeat younger than 30 s. The previous inline float(ts) could only fail.
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=3 \
    CMD python -m shared.healthcheck heartbeat db_writer 30

CMD ["python", "-m", "engines.db_writer.main"]

# syntax=docker/dockerfile:1
# Market-data service — async IB tick publisher.
#
# Installs the [ib] extra (ib-async + tzdata + base runtime). Multi-
# stage pattern shared with risk-engine ; only the CMD differs.

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

WORKDIR /app

# Minimal apt — ib_async is pure Python, numpy/scipy wheels exist for
# manylinux so build-essential is not needed at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first, from the lockfile only: this layer is cached until
# pyproject.toml / uv.lock change; --locked refuses a stale lock, so the
# image runs exactly the set CI tested (#303, audit 10#5).
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project --no-editable --extra ib
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --extra ib

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

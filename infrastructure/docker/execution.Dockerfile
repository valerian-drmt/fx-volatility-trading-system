# syntax=docker/dockerfile:1
# Execution engine — IB-connected microservice owning the order /
# trade / position lifecycle. The api forwards REST user requests to
# this service via internal HTTP.
#
# Installs the [api,ib] extras : uvicorn (HTTP server on :8001) +
# ib-insync + tzdata.
#
#   docker build -f infrastructure/docker/execution.Dockerfile -t fx-options-execution .

# Base images pinned by tag AND digest (#305, audit 10#6); Dependabot's
# docker ecosystem refreshes both together. uv installs from uv.lock (#303).
ARG PYTHON_IMAGE=python:3.11.18-slim-bookworm@sha256:a83ba18aef0f865b1a4a611c2cf460fe446f2795673bbb9fe43be570439e3b39
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490

FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE} AS base
COPY --from=uv /uv /bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH=/app/.venv/bin:$PATH

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
    uv sync --locked --no-dev --no-install-project --no-editable --extra api --extra ib
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --extra api --extra ib

# Non-root runtime (#298, audit 01#10): the image installs as root, then drops
# to an unprivileged user. The service listens above 1024 and writes nothing
# (PYTHONDONTWRITEBYTECODE, logs to stdout), so no directory needs to be writable.
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin fxvol \
    && chown -R fxvol:fxvol /app
USER fxvol

EXPOSE 8001

HEALTHCHECK --interval=15s --timeout=3s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8001/health || exit 1

CMD ["uvicorn", "engines.execution.main:app", "--host", "0.0.0.0", "--port", "8001", "--proxy-headers"]

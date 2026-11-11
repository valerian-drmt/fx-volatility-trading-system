# syntax=docker/dockerfile:1
# db-writer service — installs the [writer] extra (psycopg2-binary on
# top of base runtime). Deliberately does NOT pull ib-async, making
# this image ~150 MB lighter than the engine containers.

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

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first, from the lockfile only: this layer is cached until
# pyproject.toml / uv.lock change; --locked refuses a stale lock, so the
# image runs exactly the set CI tested (#303, audit 10#5).
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project --no-editable --extra writer
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --extra writer

RUN useradd -m -u 1004 fxvol && chown -R fxvol:fxvol /app
USER fxvol

# Same check as docker-compose.yml (one implementation, #344): ISO-8601
# heartbeat younger than 30 s. The previous inline float(ts) could only fail.
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=3 \
    CMD python -m shared.healthcheck heartbeat db_writer 30

CMD ["python", "-m", "engines.db_writer.main"]

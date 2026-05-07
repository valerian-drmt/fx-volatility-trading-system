# Market-data service — standalone async IB tick publisher.
#
# Multi-stage pattern shared by the 3 engine containers (market_data,
# vol_engine, risk_engine). Only the requirements file and CMD differ
# between them. Images land around ~450 MB each.

ARG PYTHON_IMAGE=python:3.11-slim

FROM ${PYTHON_IMAGE} AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

# Minimal apt layer — ib_insync is pure Python, numpy / scipy wheels are
# available on PyPI so build-essential is not needed at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Dependency layer — cached as long as requirements/ib.txt (and its parent
# base.txt) do not change.
COPY requirements/base.txt requirements/ib.txt ./requirements/
RUN pip install --upgrade pip && pip install -r requirements/ib.txt

# Shared code layer — changes less often than the service itself.
COPY shared/ ./shared/
COPY core/ ./core/
# The service container still reuses bus/ + persistence helpers from the
# monolith until the R7 migration is complete. Once the legacy engines
# retire we can drop ``src/`` from the image.
COPY src/ ./src/

# Service code layer — invalidated on every engine.py / main.py edit.
# services/ has no __init__.py : PEP 420 namespace package co-exists with
# the legacy src/services/ until the migration is complete.
COPY services/market_data/ ./services/market_data/

# Non-root execution.
RUN useradd -m -u 1001 fxvol && chown -R fxvol:fxvol /app
USER fxvol

# Healthcheck : TTL of the Redis heartbeat must be < 60s.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import redis, os, time; r = redis.Redis.from_url(os.environ['REDIS_URL']); ts = r.get('heartbeat:market_data'); import sys; sys.exit(0 if (ts and time.time() - float(ts) < 60) else 1)"

CMD ["python", "-m", "services.market_data.main"]

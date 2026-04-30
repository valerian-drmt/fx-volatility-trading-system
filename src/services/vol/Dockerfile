# Vol-engine service. Uses requirements/quant.txt (base + ib + arch for GARCH).

ARG PYTHON_IMAGE=python:3.11-slim

FROM ${PYTHON_IMAGE} AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl build-essential \
    && rm -rf /var/lib/apt/lists/*

# arch ships wheels for 3.11 on manylinux → build-essential is a belt.
COPY requirements/base.txt requirements/ib.txt requirements/quant.txt ./requirements/
RUN pip install --upgrade pip && pip install -r requirements/quant.txt

# Post-R9 src-layout : everything Python lives under src/. One COPY covers
# core, bus, persistence, shared, services/vol (+ other services, unused
# here but harmless -- same image size vs selective copy is negligible).
COPY src/ ./src/
# Backfill scripts (one-shot data seeding, e.g. backfill_iv_history_for_gmm.py).
COPY scripts/ ./scripts/

RUN useradd -m -u 1002 fxvol && chown -R fxvol:fxvol /app
USER fxvol

HEALTHCHECK --interval=60s --timeout=5s --start-period=240s --retries=3 \
    CMD python -c "import redis, os, time; r = redis.Redis.from_url(os.environ['REDIS_URL']); ts = r.get('heartbeat:vol_engine'); import sys; sys.exit(0 if (ts and time.time() - float(ts) < 300) else 1)"

CMD ["python", "-m", "services.vol.main"]

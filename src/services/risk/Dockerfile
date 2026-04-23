# Risk-engine service. Uses requirements/ib.txt (base + ib_insync, no GARCH).

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

COPY requirements/base.txt requirements/ib.txt ./requirements/
RUN pip install --upgrade pip && pip install -r requirements/ib.txt

# Post-R9 src-layout : all Python under src/, one COPY.
COPY src/ ./src/

RUN useradd -m -u 1003 fxvol && chown -R fxvol:fxvol /app
USER fxvol

# 2s cycle cadence — heartbeat TTL 30s tolerates one missed cycle.
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import redis, os, time; r = redis.Redis.from_url(os.environ['REDIS_URL']); ts = r.get('heartbeat:risk_engine'); import sys; sys.exit(0 if (ts and time.time() - float(ts) < 30) else 1)"

CMD ["python", "-m", "services.risk.main"]

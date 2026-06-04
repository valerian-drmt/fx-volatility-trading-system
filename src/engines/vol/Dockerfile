# Vol-engine service — installs the [quant] extra (transitively pulls [ib])
# for GARCH (arch) + GMM regime detection (scikit-learn) on top of the IB stack.

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
# pyproject.toml + src/ are needed together because PEP 517 resolves the
# package layout from src/ when installing fxvol[quant].
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --upgrade pip && pip install ".[quant]"

RUN useradd -m -u 1002 fxvol && chown -R fxvol:fxvol /app
USER fxvol

HEALTHCHECK --interval=60s --timeout=5s --start-period=240s --retries=3 \
    CMD python -c "import redis, os, time; r = redis.Redis.from_url(os.environ['REDIS_URL']); ts = r.get('heartbeat:vol_engine'); import sys; sys.exit(0 if (ts and time.time() - float(ts) < 300) else 1)"

CMD ["python", "-m", "engines.vol.main"]

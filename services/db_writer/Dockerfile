# db-writer service. Uses requirements/writer.txt — base + SQLAlchemy +
# asyncpg + alembic. Deliberately does NOT pull ib_insync, making this
# image ~150 MB lighter than the engine containers.

ARG PYTHON_IMAGE=python:3.11-slim

FROM ${PYTHON_IMAGE} AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/base.txt requirements/writer.txt ./requirements/
RUN pip install --upgrade pip && pip install -r requirements/writer.txt

COPY shared/ ./shared/
COPY core/ ./core/
COPY src/ ./src/
COPY persistence/ ./persistence/
COPY services/db_writer/ ./services/db_writer/

RUN useradd -m -u 1004 fxvol && chown -R fxvol:fxvol /app
USER fxvol

HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import redis, os, time; r = redis.Redis.from_url(os.environ['REDIS_URL']); ts = r.get('heartbeat:db_writer'); import sys; sys.exit(0 if (ts and time.time() - float(ts) < 30) else 1)"

CMD ["python", "-m", "services.db_writer.main"]

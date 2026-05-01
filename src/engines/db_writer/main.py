"""Entrypoint for the db-writer container.

Subscribes Redis → queues events → AsyncDatabaseWriter flushes to Postgres
every 5 s (batched). Graceful SIGTERM stops the subscribe, drains the
queue, commits the last batch, then exits.
"""
from __future__ import annotations

import asyncio
import signal

from persistence.writer import AsyncDatabaseWriter
from engines.db_writer.service import DbWriterService
from shared.config import get_settings
from shared.logging import configure_logging
from bus import get_async_redis


async def run() -> None:
    settings = get_settings()
    configure_logging(
        service_name=settings.SERVICE_NAME or "db_writer", level=settings.LOG_LEVEL
    )

    if not settings.DATABASE_URL:
        raise RuntimeError("DATABASE_URL env var is required for db-writer")

    writer = AsyncDatabaseWriter(database_url=settings.DATABASE_URL)
    redis = get_async_redis()
    service = DbWriterService(redis=redis, writer=writer)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, service.request_stop)
        except NotImplementedError:
            signal.signal(sig, lambda _s, _f: service.request_stop())

    await service.run()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()

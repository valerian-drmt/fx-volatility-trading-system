"""DbWriterThread — hosts an AsyncDatabaseWriter on a dedicated asyncio loop.

Bridges the sync PyQt / threading.Thread world (engines) to the async
SQLAlchemy writer. The thread owns its event loop ; callers from other
threads use ``enqueue(table_name, payload)`` which schedules a
``put_nowait`` on that loop via ``loop.call_soon_threadsafe`` — the only
documented thread-safe way to hand work to a running asyncio loop.

Lifecycle :
    t = DbWriterThread(database_url=url)
    t.start()                       # spawns thread, creates loop + writer
    t.wait_until_ready(timeout=5)   # blocks until writer is accepting events
    t.enqueue("vol_surfaces", {...})
    ...
    t.stop()                        # schedules writer.shutdown(), joins

Reference : releases/architecture_finale_project/07-async-db-writer.md
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from persistence.writer import AsyncDatabaseWriter

logger = logging.getLogger(__name__)


class DbWriterThread(threading.Thread):
    """Run an ``AsyncDatabaseWriter`` on its own event loop in a daemon thread.

    The writer is created inside ``run()`` so its internal ``asyncio.Queue``
    binds to the correct loop. Passing the writer from outside would break
    because an ``asyncio.Queue`` built on loop A cannot be consumed on loop B.
    """

    READY_TIMEOUT_S: float = 5.0
    STOP_TIMEOUT_S: float = 10.0

    def __init__(self, **writer_kwargs: Any) -> None:
        super().__init__(name="DbWriter", daemon=True)
        self._writer_kwargs = writer_kwargs
        self._loop: asyncio.AbstractEventLoop | None = None
        self._writer: AsyncDatabaseWriter | None = None
        self._ready = threading.Event()

    def run(self) -> None:  # threading.Thread entrypoint
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._writer = AsyncDatabaseWriter(**self._writer_kwargs)
            self._ready.set()
            self._loop.run_until_complete(self._writer.run())
        except Exception:
            logger.exception("DbWriterThread crashed")
        finally:
            try:
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            except Exception:
                logger.exception("DbWriterThread: shutdown_asyncgens error")
            self._loop.close()

    def wait_until_ready(self, timeout: float | None = None) -> bool:
        """Block until the writer is instantiated and accepting events."""
        return self._ready.wait(timeout if timeout is not None else self.READY_TIMEOUT_S)

    def enqueue(self, table_name: str, payload: dict) -> None:
        """Thread-safe : schedule a ``put_nowait`` on the writer's loop.

        Non-blocking best effort : if the writer is not up yet, or the queue
        is full, the event is logged and dropped. Producers MUST NOT wait
        on this from engine threads — the whole point of the writer is
        decoupling.
        """
        if self._writer is None or self._loop is None:
            logger.warning("db writer not ready, dropping event for %s", table_name)
            return
        try:
            self._loop.call_soon_threadsafe(self._put_nowait, table_name, payload)
        except RuntimeError:
            # Loop already closed (shutdown raced with a late enqueue).
            logger.warning("db writer loop closed, dropping event for %s", table_name)

    def _put_nowait(self, table_name: str, payload: dict) -> None:
        """Runs on the writer's loop thread, called by ``call_soon_threadsafe``."""
        assert self._writer is not None
        try:
            self._writer.queue.put_nowait((table_name, payload))
        except asyncio.QueueFull:
            logger.error("db writer queue full, dropping event for %s", table_name)

    def stop(self, timeout: float | None = None) -> None:
        """Trigger graceful shutdown on the writer and join the thread."""
        join_timeout = timeout if timeout is not None else self.STOP_TIMEOUT_S
        if self._writer is not None and self._loop is not None and self._loop.is_running():
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    self._writer.shutdown(), self._loop
                )
                fut.result(timeout=join_timeout)
            except Exception:
                logger.exception("DbWriterThread.stop: shutdown error")
        if self.is_alive():
            self.join(timeout=join_timeout)

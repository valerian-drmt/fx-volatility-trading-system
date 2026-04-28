"""Shared utilities consumed by every R7 service container (market-data,
vol-engine, risk, db-writer) and — transitionally — by the legacy
PyQt Controller.

What belongs here vs. where :

* ``core/``   — pure domain math (no I/O). Example : Black-Scholes formula.
* ``shared/`` — stateful helpers (pools, retry loops, logging, env config).
  Example : ``connect_ib_with_backoff``.
* ``services/<name>/`` — the runtime of a single container (engine loop,
  Redis subscribe, graceful shutdown). Never shared between services.

Importing between two ``services/<name>/`` packages is explicitly banned
(see R7 spec annex rule #2). All cross-service code routes through
``shared/`` or ``core/``.
"""
from shared.config import Settings, get_settings
from shared.db_queue import publish_db_event
from shared.ib_connection import connect_ib_with_backoff, next_backoff_seconds
from shared.logging import configure_logging
from shared.redis_client import get_async_redis, reset_for_tests

__all__ = [
    "Settings",
    "configure_logging",
    "connect_ib_with_backoff",
    "get_async_redis",
    "get_settings",
    "next_backoff_seconds",
    "publish_db_event",
    "reset_for_tests",
]

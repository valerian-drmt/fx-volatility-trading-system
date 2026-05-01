"""Shared utilities consumed by every service container.

What belongs here vs. where :

* ``core/``   — pure domain math (no I/O). Example : Black-Scholes formula.
* ``bus/``    — Redis adapter (publisher + client factories + channel
                 constants). Single source of truth for Redis access.
* ``shared/`` — cross-cutting infra helpers that are not Redis nor DB :
                 config (Settings), structured logging, IB connection
                 wrapper, db-events publisher.
* ``services/<name>/`` — the runtime of a single container (engine loop,
                 graceful shutdown). Never shared between services ;
                 cross-service code routes through ``bus/`` or ``shared/``.
"""
from shared.config import Settings, get_settings
from shared.db_events import publish_db_event
from shared.ib_connection import connect_ib_with_backoff, next_backoff_seconds
from shared.logging import configure_logging

__all__ = [
    "Settings",
    "configure_logging",
    "connect_ib_with_backoff",
    "get_settings",
    "next_backoff_seconds",
    "publish_db_event",
]

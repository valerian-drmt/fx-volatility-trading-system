"""structlog configuration shared by every service container.

Format choice : JSON to stdout. Each record carries ``service_name`` so
``docker logs market-data -f`` and centralised aggregators (Grafana
Loki) can filter without regex-parsing a plain-text prefix.
"""
from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(service_name: str, level: str = "INFO") -> None:
    """Wire structlog + stdlib logging to emit JSON to stdout.

    Safe to call multiple times — later calls overwrite the configuration
    (useful for tests that change ``service_name``).
    """
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        stream=sys.stdout,
        force=True,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    # Stamp service_name so it's present on every log line emitted from
    # anywhere in the process (no need to thread it through every logger).
    structlog.contextvars.bind_contextvars(service_name=service_name)

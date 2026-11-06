"""structlog configuration shared by every service container.

Format choice : JSON to stdout. Each record carries ``service_name`` so
``docker logs market-data -f`` and centralised aggregators (Grafana
Loki) can filter without regex-parsing a plain-text prefix.
"""
from __future__ import annotations

import logging
import sys

import structlog

from shared.redact import install_stdlib_redaction, redact_event_dict


def configure_logging(service_name: str, level: str = "INFO") -> None:
    """Wire structlog + stdlib logging to emit JSON to stdout.

    Safe to call multiple times — later calls overwrite the configuration
    (useful for tests that change ``service_name``).

    Secrets never reach stdout: ``redact_event_dict`` scrubs every structlog
    event (URL passwords, ``key=value`` credentials) right before rendering,
    and the same scrubber is installed as a filter on the stdlib handlers
    for the modules that still use ``logging.getLogger`` (#297).
    """
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        stream=sys.stdout,
        force=True,
    )
    install_stdlib_redaction()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            redact_event_dict,
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

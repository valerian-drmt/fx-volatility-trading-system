"""One logging pipeline for every service (#336, audit 06#12).

Both logger families end in the same JSON line on stdout:

* ``structlog.get_logger()`` events go through the shared processors and
  are handed to the stdlib root handler;
* ``logging.getLogger()`` records (41 modules still use it) are rendered
  by the same ``ProcessorFormatter``: ``extra={...}`` fields are merged in
  (``ExtraAdder``), the contextvars (``service_name``, ``trace_id``,
  ``cycle_id``) are attached, and the secret scrubber runs last, so a
  stdlib ``extra=`` field or a trace id is never dropped on the stdlib path
  the way ``basicConfig(format="%(message)s")`` dropped them.

``get_logger(name)`` is the one factory new code should use; it returns a
structlog bound logger wired to the stdlib logger of that name.
"""
from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from shared.redact import RedactingFilter, redact_event_dict

# Processors shared by both families (run before rendering).
_SHARED_PROCESSORS: list[Any] = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_logger_name,
    structlog.stdlib.add_log_level,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    structlog.processors.StackInfoRenderer(),
]


def configure_logging(service_name: str, level: str = "INFO") -> None:
    """Wire structlog and stdlib logging to emit JSON to stdout.

    Safe to call multiple times — later calls overwrite the configuration
    (useful for tests that change ``service_name``). Secrets never reach
    stdout: ``redact_event_dict`` scrubs every event dict right before
    rendering, and ``RedactingFilter`` scrubs the formatted stdlib message
    (#297).
    """
    lvl = getattr(logging, level.upper(), logging.INFO)

    formatter = structlog.stdlib.ProcessorFormatter(
        # Records born in stdlib: merge ``extra=`` fields, then the shared chain.
        foreign_pre_chain=[structlog.stdlib.ExtraAdder(), *_SHARED_PROCESSORS],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            redact_event_dict,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(RedactingFilter())

    root = logging.getLogger()
    for old in list(root.handlers):
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(lvl)

    structlog.configure(
        processors=[*_SHARED_PROCESSORS, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.make_filtering_bound_logger(lvl),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )
    # Stamp service_name so it's present on every log line emitted from
    # anywhere in the process (no need to thread it through every logger).
    structlog.contextvars.bind_contextvars(service_name=service_name)


def get_logger(name: str | None = None) -> Any:
    """The one logger factory: a structlog bound logger on the stdlib logger
    ``name``, so it obeys the root level and handler configured above."""
    return structlog.get_logger(name) if name else structlog.get_logger()

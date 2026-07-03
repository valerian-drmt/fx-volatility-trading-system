"""Structured JSON logging via structlog — one log line per request.

Every request is stamped with a correlation ``trace_id`` (honoured from an
inbound ``X-Trace-ID`` header, else minted) bound to the structlog contextvars,
so *every* log line in the request — and every downstream service it calls —
carries the same id. ``merge_contextvars`` in the processor chain is what makes
that automatic (see ``shared.trace``).
"""
from __future__ import annotations

import logging
import time

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from shared.trace import TRACE_HEADER, bind_trace_id, clear_trace_id, new_trace_id


def configure_logging(level: str = "INFO") -> None:
    """Wire structlog to stdlib logging with JSON renderer (idempotent)."""
    logging.basicConfig(level=level.upper(), format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,  # surfaces trace_id on every line
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Bind a correlation trace_id for the request, then log one JSON line."""

    async def dispatch(self, request: Request, call_next):
        trace_id = request.headers.get(TRACE_HEADER) or new_trace_id()
        bind_trace_id(trace_id)
        log = structlog.get_logger("api.access")
        start = time.monotonic()
        status = 500  # if call_next raises, the access line still records a 5xx
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers[TRACE_HEADER] = trace_id  # let the client keep the id
            return response
        finally:
            log.info(
                "http_request",
                method=request.method,
                path=request.url.path,
                status=status,
                duration_ms=round((time.monotonic() - start) * 1000, 2),
            )
            clear_trace_id()

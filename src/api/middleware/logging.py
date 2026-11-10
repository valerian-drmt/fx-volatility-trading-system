"""Structured JSON logging via structlog — one log line per request.

Every request is stamped with a correlation ``trace_id`` (honoured from an
inbound ``X-Trace-ID`` header, else minted) bound to the structlog contextvars,
so *every* log line in the request — and every downstream service it calls —
carries the same id. ``merge_contextvars`` in the processor chain is what makes
that automatic (see ``shared.trace``).
"""
from __future__ import annotations

import time

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from shared.logging import configure_logging as _shared_configure_logging
from shared.trace import (
    REQUEST_START_HEADER,
    TRACE_HEADER,
    bind_request_start,
    bind_trace_id,
    clear_request_start,
    clear_trace_id,
    new_trace_id,
)


def configure_logging(level: str = "INFO") -> None:
    """The api uses the one shared pipeline (#336): ``shared.logging``
    bridges stdlib records through structlog's ``ProcessorFormatter`` so
    ``extra=`` fields and the request's ``trace_id`` reach the JSON line,
    and the secret scrubber runs on every record (#297)."""
    _shared_configure_logging(service_name="api", level=level)


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Bind a correlation trace_id for the request, then log one JSON line."""

    async def dispatch(self, request: Request, call_next):
        trace_id = request.headers.get(TRACE_HEADER) or new_trace_id()
        bind_trace_id(trace_id)
        bind_request_start(request.headers.get(REQUEST_START_HEADER))  # #370
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
            clear_request_start()

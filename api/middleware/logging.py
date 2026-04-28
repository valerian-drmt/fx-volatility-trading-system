"""Structured JSON logging via structlog — one log line per request."""
from __future__ import annotations

import logging
import time

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


def configure_logging(level: str = "INFO") -> None:
    """Wire structlog to stdlib logging with JSON renderer (idempotent)."""
    logging.basicConfig(level=level.upper(), format="%(message)s")
    structlog.configure(
        processors=[
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
    """Log method, path, status, duration_ms as a single JSON line per request."""

    async def dispatch(self, request: Request, call_next):
        log = structlog.get_logger("api.access")
        start = time.monotonic()
        response = await call_next(request)
        log.info(
            "http_request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round((time.monotonic() - start) * 1000, 2),
        )
        return response

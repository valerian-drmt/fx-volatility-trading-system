"""Prometheus histogram on request duration — labels : method, path_template, status."""
from __future__ import annotations

import time

from prometheus_client import Histogram
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

REQUEST_DURATION = Histogram(
    "fxvol_http_request_duration_seconds",
    "HTTP request duration by method/path/status.",
    labelnames=("method", "path", "status"),
)


class TimingMiddleware(BaseHTTPMiddleware):
    """Populate REQUEST_DURATION histogram on every response."""

    async def dispatch(self, request: Request, call_next):
        start = time.monotonic()
        response = await call_next(request)
        # ``request.url.path`` is the concrete URL, not the template — good
        # enough for low-cardinality ops ; we can swap to route.path later.
        REQUEST_DURATION.labels(
            method=request.method,
            path=request.url.path,
            status=response.status_code,
        ).observe(time.monotonic() - start)
        return response

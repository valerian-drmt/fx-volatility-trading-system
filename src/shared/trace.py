"""Correlation (trace) id — one id threaded request → order → fill → position.

Bound to structlog contextvars, so every log line emitted in the same async
context carries it automatically (``merge_contextvars`` is in the processor
chain — see ``shared.logging``). Propagated across services via the
``X-Trace-ID`` header, and (once persisted on the trade rows) re-bindable when
async fills / reconcile handle a structure long after the originating request.

The point: any "why didn't this fill / close / book?" becomes a single
``grep <trace_id>`` across all service logs instead of manual log+SQL forensics.
"""
from __future__ import annotations

import time
import uuid
from contextvars import ContextVar

import structlog

TRACE_HEADER = "X-Trace-ID"
# Epoch seconds at which the FIRST service received the operator's request;
# forwarded api -> execution so the submit latency (#370) is measured from
# the click, not from the hop.
REQUEST_START_HEADER = "X-Request-Start"
_TRACE_KEY = "trace_id"
_request_start: ContextVar[float | None] = ContextVar("fxvol_request_start", default=None)


def new_trace_id() -> str:
    """A fresh short correlation id (16 hex chars — plenty for a desk)."""
    return uuid.uuid4().hex[:16]


def current_trace_id() -> str | None:
    """The trace id bound to the current context, if any."""
    return structlog.contextvars.get_contextvars().get(_TRACE_KEY)


def bind_trace_id(trace_id: str) -> None:
    """Bind ``trace_id`` so every subsequent log line in this context carries it."""
    structlog.contextvars.bind_contextvars(**{_TRACE_KEY: trace_id})


def clear_trace_id() -> None:
    """Unbind the trace id (call at the end of a request to avoid leaking it)."""
    structlog.contextvars.unbind_contextvars(_TRACE_KEY)


def bind_request_start(header_value: str | None = None) -> float:
    """Bind the request start for this context: the upstream's stamp when the
    header parses, else now. Returns the bound value."""
    start: float | None = None
    if header_value:
        try:
            start = float(header_value)
        except ValueError:
            start = None
    if start is None or not (0 < start < time.time() + 60):
        start = time.time()
    _request_start.set(start)
    return start


def current_request_start() -> float | None:
    return _request_start.get()


def clear_request_start() -> None:
    _request_start.set(None)


def trace_headers() -> dict[str, str]:
    """Header dict propagating the current trace id (and the request start,
    when bound) to a downstream service."""
    out: dict[str, str] = {}
    tid = current_trace_id()
    if tid:
        out[TRACE_HEADER] = tid
    start = current_request_start()
    if start is not None:
        out[REQUEST_START_HEADER] = f"{start:.3f}"
    return out

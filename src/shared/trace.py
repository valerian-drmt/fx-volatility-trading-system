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

import uuid

import structlog

TRACE_HEADER = "X-Trace-ID"
_TRACE_KEY = "trace_id"


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


def trace_headers() -> dict[str, str]:
    """Header dict propagating the current trace id to a downstream service."""
    tid = current_trace_id()
    return {TRACE_HEADER: tid} if tid else {}

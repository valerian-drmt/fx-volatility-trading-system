"""Correlation trace-id helpers (shared.trace) + the API access middleware
binding one per request and echoing it in the X-Trace-ID response header.
"""
from __future__ import annotations

import structlog


def test_trace_id_roundtrip_and_headers():
    from shared.trace import (
        bind_trace_id,
        clear_trace_id,
        current_trace_id,
        new_trace_id,
        trace_headers,
    )

    assert current_trace_id() is None
    assert trace_headers() == {}

    tid = new_trace_id()
    assert len(tid) == 16 and all(c in "0123456789abcdef" for c in tid)
    assert new_trace_id() != tid  # fresh each call

    bind_trace_id(tid)
    try:
        assert current_trace_id() == tid
        assert trace_headers() == {"X-Trace-ID": tid}
    finally:
        clear_trace_id()
    assert current_trace_id() is None


def test_bound_trace_id_appears_on_log_lines():
    """merge_contextvars in the chain must surface a bound trace_id automatically."""
    from api.middleware.logging import configure_logging
    from shared.trace import bind_trace_id, clear_trace_id

    cap = structlog.testing.LogCapture()
    structlog.configure(processors=[structlog.contextvars.merge_contextvars, cap])
    bind_trace_id("abc123def456aaaa")
    try:
        structlog.get_logger("t").info("hello")
        assert cap.entries[0]["trace_id"] == "abc123def456aaaa"
    finally:
        clear_trace_id()
        configure_logging("INFO")  # restore the normal chain (don't leak into other tests)


def test_access_middleware_stamps_response_header():
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from api.middleware.logging import AccessLogMiddleware
    from shared.trace import TRACE_HEADER

    app = FastAPI()
    app.add_middleware(AccessLogMiddleware)

    @app.get("/ping")
    def ping() -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(app)
    # minted when absent
    r1 = client.get("/ping")
    assert r1.headers.get(TRACE_HEADER) and len(r1.headers[TRACE_HEADER]) == 16
    # honoured when the caller supplies one (propagates across a hop)
    r2 = client.get("/ping", headers={TRACE_HEADER: "feedfacefeedface"})
    assert r2.headers[TRACE_HEADER] == "feedfacefeedface"

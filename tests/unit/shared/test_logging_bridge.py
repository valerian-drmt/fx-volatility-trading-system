"""One logging pipeline (#336, audit 06#12): a stdlib ``extra=`` field, the
bound trace id and the service name reach the JSON line; the structlog path
renders the same shape; the scrubber runs on both."""
from __future__ import annotations

import io
import json
import logging
import sys

import pytest
import structlog

from shared.logging import configure_logging, get_logger


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch):
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    configure_logging(service_name="test_svc", level="INFO")
    yield buf
    structlog.contextvars.clear_contextvars()


def _lines(buf: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]


def test_stdlib_extra_fields_and_contextvars_reach_the_json_line(captured: io.StringIO) -> None:
    structlog.contextvars.bind_contextvars(trace_id="abc123")
    logging.getLogger("engines.execution.fills").info("fill_persisted", extra={"order_id": 7, "qty": 5})
    (rec,) = _lines(captured)
    assert rec["event"] == "fill_persisted"
    assert rec["order_id"] == 7 and rec["qty"] == 5, "extra= fields survive the stdlib path"
    assert rec["trace_id"] == "abc123" and rec["service_name"] == "test_svc"
    assert rec["level"] == "info" and rec["logger"] == "engines.execution.fills" and "timestamp" in rec


def test_structlog_and_stdlib_render_the_same_shape(captured: io.StringIO) -> None:
    get_logger("api.lifespan").info("api_startup", port=8000)
    logging.getLogger("api.lifespan").info("api_startup", extra={"port": 8000})
    a, b = _lines(captured)
    assert a["event"] == b["event"] == "api_startup" and a["port"] == b["port"] == 8000
    assert a["service_name"] == b["service_name"] == "test_svc"


def test_secrets_are_scrubbed_on_both_paths(captured: io.StringIO) -> None:
    from shared.redact import MASK

    logging.getLogger("x").warning("connect failed for redis://:hunter2@redis:6379/0")
    get_logger("x").warning("db", url="postgresql+asyncpg://fxvol:hunter2@pg/fxvol")
    a, b = _lines(captured)
    assert "hunter2" not in json.dumps(a) and MASK in a["event"]
    assert "hunter2" not in json.dumps(b) and MASK in b["url"]


def test_exceptions_render_on_the_stdlib_path(captured: io.StringIO) -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("x").exception("step_failed", extra={"step": "refit"})
    (rec,) = _lines(captured)
    assert rec["step"] == "refit" and "ValueError: boom" in rec["exception"] and rec["level"] == "error"


def test_level_filters_both_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    configure_logging(service_name="t", level="WARNING")
    logging.getLogger("x").info("dropped")
    get_logger("x").info("dropped_too")
    logging.getLogger("x").warning("kept")
    assert [r["event"] for r in _lines(buf)] == ["kept"]
    structlog.contextvars.clear_contextvars()

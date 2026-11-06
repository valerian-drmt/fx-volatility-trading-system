"""Secret redaction in the logging pipeline (audit 01 F-02 / #297).

The leak that motivated this: ``log.info("api_startup", redis_url=...)``
printed ``redis://:<password>@redis:6379/0`` at every api start. These tests
pin that no path — structlog event, stdlib %-formatted message, nested dict —
can emit a URL password or a ``key=value`` credential again.
"""
from __future__ import annotations

import io
import logging

import pytest
import structlog

from shared.redact import (
    MASK,
    RedactingFilter,
    install_stdlib_redaction,
    redact_event_dict,
    redact_secrets,
    redact_url,
)

PW = "s3cr3t-Pa55"


@pytest.mark.parametrize(
    "raw, expected",
    [
        (f"redis://:{PW}@redis:6379/0", f"redis://:{MASK}@redis:6379/0"),
        (f"postgresql+asyncpg://fxvol:{PW}@postgres:5432/fxvol",
         f"postgresql+asyncpg://fxvol:{MASK}@postgres:5432/fxvol"),
        (f"connect failed for amqp://guest:{PW}@mq/ after 3 tries",
         f"connect failed for amqp://guest:{MASK}@mq/ after 3 tries"),
        (f"password={PW} user=fxvol", f"password={MASK} user=fxvol"),
        (f'{{"token": "{PW}"}}', f'{{"token": "{MASK}"}}'),
        (f"Authorization: Bearer {PW}", f"Authorization: {MASK}"),
    ],
)
def test_redact_secrets_masks_credentials(raw: str, expected: str) -> None:
    out = redact_secrets(raw)
    assert out == expected
    assert PW not in out


def test_redact_secrets_leaves_plain_text_and_credential_free_urls_alone() -> None:
    assert redact_secrets("http://execution-engine:8001/internal/orders") == (
        "http://execution-engine:8001/internal/orders"
    )
    assert redact_secrets("trade_submit_failed preview_id=abc status=409") == (
        "trade_submit_failed preview_id=abc status=409"
    )
    assert redact_secrets("") == ""


def test_redact_url_keeps_host_port_path_only() -> None:
    assert redact_url(f"redis://:{PW}@redis:6379/0") == "redis://redis:6379/0"
    assert redact_url(f"postgresql+asyncpg://fxvol:{PW}@postgres:5432/fxvol") == (
        "postgresql+asyncpg://postgres:5432/fxvol"
    )
    assert redact_url("redis://redis/1") == "redis://redis/1"
    assert redact_url(None) is None
    assert redact_url("") == ""


def test_redact_url_never_returns_the_password_on_odd_input() -> None:
    assert PW not in (redact_url(f"not a url but password={PW}") or "")


def test_structlog_processor_scrubs_nested_values_and_secret_keys() -> None:
    event = {
        "event": f"connecting to redis://:{PW}@redis:6379/0",
        "redis_url": f"redis://:{PW}@redis:6379/0",
        "password": PW,
        "DB_PASSWORD": PW,
        "ctx": {"dsn": f"postgresql://u:{PW}@h/db", "items": [f"token={PW}", 3]},
        "n": 42,
    }
    out = redact_event_dict(None, "info", event)
    flat = repr(out)
    assert PW not in flat
    assert out["password"] == MASK
    assert out["DB_PASSWORD"] == MASK
    assert out["n"] == 42
    assert out["ctx"]["items"][1] == 3


def test_structlog_chain_with_processor_emits_no_password(capsys: pytest.CaptureFixture[str]) -> None:
    structlog.configure(
        processors=[redact_event_dict, structlog.processors.JSONRenderer()],
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )
    try:
        structlog.get_logger("t").info("api_startup", redis_url=f"redis://:{PW}@redis:6379/0")
    finally:
        structlog.reset_defaults()
    out = capsys.readouterr().out
    assert "api_startup" in out
    assert PW not in out
    assert MASK in out


def test_stdlib_filter_scrubs_percent_formatted_messages() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactingFilter())
    lg = logging.getLogger("test_redact.stdlib")
    lg.handlers.clear()
    lg.addHandler(handler)
    lg.propagate = False
    lg.setLevel(logging.INFO)
    try:
        lg.error("db connect failed url=%s exc=%s", f"postgresql://u:{PW}@h/db", f"password={PW}")
        lg.info("plain line with no secret, status=%s", 200)
    finally:
        lg.removeHandler(handler)
    out = stream.getvalue()
    assert PW not in out
    assert "plain line with no secret, status=200" in out


def test_install_stdlib_redaction_is_idempotent_on_root_handlers() -> None:
    root = logging.getLogger()
    handler = logging.StreamHandler(io.StringIO())
    root.addHandler(handler)
    try:
        install_stdlib_redaction()
        install_stdlib_redaction()
        assert sum(isinstance(f, RedactingFilter) for f in handler.filters) == 1
    finally:
        root.removeHandler(handler)


def test_both_configure_logging_entry_points_redact(capsys: pytest.CaptureFixture[str]) -> None:
    """The api and the engines each have a configure_logging; both must scrub."""
    from api.middleware.logging import configure_logging as api_configure
    from shared.logging import configure_logging as engine_configure

    for configure in (lambda: engine_configure("t_engine", "INFO"), lambda: api_configure("INFO")):
        configure()
        structlog.get_logger("t").info("startup", redis_url=f"redis://:{PW}@redis:6379/0")
        logging.getLogger("t.std").error("dsn=%s", f"postgresql://u:{PW}@h/db")
        out = capsys.readouterr().out
        assert PW not in out, out
    structlog.contextvars.clear_contextvars()

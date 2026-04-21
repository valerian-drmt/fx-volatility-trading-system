"""R7 PR #8 : verify ``ENGINES_IN_PROCESS=false`` disables the in-process
engine startup.

The Controller's ``_start_engine_pool`` is the gate — we don't boot a
full PyQt UI here, just build a Controller-like stub and call the
method directly with the env toggled. All engine constructors are
mocked so the test only validates the branching.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from controller import _engines_in_process_enabled


@pytest.mark.unit
@pytest.mark.parametrize(
    "value,expected",
    [
        ("true", True),
        ("TRUE", True),
        ("1", True),
        ("", True),  # empty string → default-on, R7 backwards compat
        ("false", False),
        ("False", False),
        ("0", False),
        ("no", False),
        ("off", False),
    ],
)
def test_engines_in_process_flag_parsing(monkeypatch, value, expected):
    if value == "":
        monkeypatch.delenv("ENGINES_IN_PROCESS", raising=False)
    else:
        monkeypatch.setenv("ENGINES_IN_PROCESS", value)
    assert _engines_in_process_enabled() is expected


@pytest.mark.unit
def test_default_is_enabled_when_env_unset(monkeypatch):
    monkeypatch.delenv("ENGINES_IN_PROCESS", raising=False)
    assert _engines_in_process_enabled() is True


@pytest.mark.unit
def test_start_engine_pool_short_circuits_when_flag_false(monkeypatch):
    """_start_engine_pool must not instantiate any engine class when disabled."""
    monkeypatch.setenv("ENGINES_IN_PROCESS", "false")

    from controller import Controller

    # Build a Controller without running its heavy __init__ — we only need
    # the bound method and a handful of attributes the short-circuit touches.
    c = Controller.__new__(Controller)
    c._log = MagicMock()
    c.ib_client = MagicMock()
    c.tick_interval_ms = 500
    c.market_symbol = "EURUSD"
    c.host = "127.0.0.1"
    c.port = 4002
    c.window = None
    c._check_market_open = MagicMock(return_value=True)
    c._start_db_writer_thread = MagicMock()
    c._post_ui = MagicMock()

    with patch("controller.MarketDataEngine") as md, patch(
        "controller.VolEngine"
    ) as vol, patch("controller.RiskEngine") as risk:
        c._start_engine_pool()

    md.assert_not_called()
    vol.assert_not_called()
    risk.assert_not_called()
    c._start_db_writer_thread.assert_not_called()
    c._log.assert_called()
    # The log message mentions the R7 fallback so ops know what happened.
    log_text = " ".join(args[0] for args, _ in c._log.call_args_list)
    assert "ENGINES_IN_PROCESS" in log_text

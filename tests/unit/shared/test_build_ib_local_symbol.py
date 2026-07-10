"""Unit tests for build_ib_local_symbol — the inverse of parse_local_symbol.

Lets an unfilled leg show its contract (e.g. "EUUV6 C1160") before IB stamps the
real symbol on fill, instead of a bare "—".
"""
from __future__ import annotations

from datetime import date

from shared.contracts import build_ib_local_symbol, parse_local_symbol


def test_call_and_put_option_symbols() -> None:
    # October 2026 (month letter V, year digit 6), strike ×1000, 4 digits.
    assert build_ib_local_symbol("call", date(2026, 10, 15), 1.160, "EUR") == "EUUV6 C1160"
    assert build_ib_local_symbol("put", date(2026, 10, 15), 1.130, "EUR") == "EUUV6 P1130"


def test_future_symbols() -> None:
    assert build_ib_local_symbol("future", date(2026, 12, 20), None, "EUR") == "6EZ6"
    assert build_ib_local_symbol("future", date(2026, 12, 20), None, "M6E") == "M6EZ6"


def test_round_trips_through_parse() -> None:
    # The built symbol must decode back to the same contract kind + strike.
    ls = build_ib_local_symbol("call", date(2026, 10, 15), 1.160, "EUR")
    spec = parse_local_symbol(ls)
    assert spec is not None
    assert spec.instrument_type == "OPTION"
    assert spec.option_type == "CALL"
    assert spec.strike == 1.160


def test_insufficient_fields_return_none() -> None:
    assert build_ib_local_symbol("call", None, 1.16, "EUR") is None          # no expiry
    assert build_ib_local_symbol("call", date(2026, 10, 15), None, "EUR") is None  # no strike
    assert build_ib_local_symbol("banana", date(2026, 10, 15), 1.16, "EUR") is None  # bad type

"""Unit tests for the stuck-order → live-IB-position matcher."""
from __future__ import annotations

from types import SimpleNamespace

from engines.execution.order_reconciler import _leg_matches_position


def _order(side, ct, strike):
    return SimpleNamespace(side=side, contract_type=ct, contract_strike=strike)


def _pos(side, structure):
    return SimpleNamespace(side=side, structure=structure)


def test_matches_put_same_side_type_strike():
    assert _leg_matches_position(_order("SELL", "put", 1.145), _pos("SELL", "EUUQ6 P1145")) is True


def test_matches_call():
    assert _leg_matches_position(_order("BUY", "call", 1.150), _pos("BUY", "EUUU6 C1150")) is True


def test_no_match_on_side():
    assert _leg_matches_position(_order("BUY", "put", 1.145), _pos("SELL", "EUUQ6 P1145")) is False


def test_no_match_on_right():
    # order is a call but the live position is a put
    assert _leg_matches_position(_order("SELL", "call", 1.145), _pos("SELL", "EUUQ6 P1145")) is False


def test_no_match_on_strike():
    assert _leg_matches_position(_order("BUY", "call", 1.20), _pos("BUY", "EUUU6 C1150")) is False


def test_strike_within_grid_tolerance():
    # 1.1503 rounds onto the same 0.005 grid point as C1150 → still a match
    assert _leg_matches_position(_order("BUY", "call", 1.1503), _pos("BUY", "EUUU6 C1150")) is True


def test_matches_future():
    assert _leg_matches_position(_order("BUY", "future", None), _pos("BUY", "6EU6")) is True


def test_future_order_does_not_match_option_position():
    assert _leg_matches_position(_order("BUY", "future", None), _pos("BUY", "EUUU6 C1150")) is False

"""`serialize_spot_order` — the `/trade/spot-orders` payload the Orders blotter
merges with `/trade/submitted` (#291)."""
from __future__ import annotations

from datetime import UTC, datetime

from api.routers.trade import serialize_spot_order
from persistence.models import SpotOrder

TS = datetime(2026, 11, 5, 10, 5, tzinfo=UTC)


def _row(**over) -> SpotOrder:
    base = dict(
        id=7, created_at=TS, symbol="EUR", currency="USD", exchange="IDEALPRO",
        side="SELL", qty=44_513, order_type="LMT", limit_price=1.12325,
        ib_order_id="5", ib_perm_id="422816514", ib_local_symbol="EUR.USD",
        state="submitted", state_updated_at=TS, qty_filled=0,
        avg_fill_price=None, commission_usd=None, filled_at=None, rejection_text=None,
    )
    base.update(over)
    return SpotOrder(**base)


def test_serializes_a_working_order_with_its_ib_cancel_handle():
    out = serialize_spot_order(_row())
    assert out["id"] == 7
    assert out["local_symbol"] == "EUR.USD"
    assert out["side"] == "SELL" and out["qty"] == 44_513 and out["qty_filled"] == 0
    assert out["order_type"] == "LMT" and out["limit_price"] == 1.12325
    assert out["state"] == "submitted"
    assert out["ib_order_id"] == 5  # int: the DELETE /orders/{id} handle


def test_local_symbol_falls_back_before_ib_names_it_and_bad_ids_are_null():
    out = serialize_spot_order(_row(ib_local_symbol=None, ib_order_id=None))
    assert out["local_symbol"] == "EUR.USD"
    assert out["ib_order_id"] is None
    out = serialize_spot_order(_row(ib_order_id="n/a"))
    assert out["ib_order_id"] is None


def test_filled_row_carries_fill_economics():
    out = serialize_spot_order(_row(
        state="filled", qty_filled=44_513, avg_fill_price=1.12326, commission_usd=2.0, filled_at=TS,
    ))
    assert out["state"] == "filled"
    assert out["avg_fill_price"] == 1.12326
    assert out["commission_usd"] == 2.0
    assert out["filled_at"] == TS

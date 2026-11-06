"""Unit tests for engines.execution.spot_orders — the persisted lifecycle of a
spot FX (CASH) order placed from the Trade tab's cash ticket (#291).

Covers :
  * the row is written ``pending`` before placement and gets IB ids + the
    status IB already reports right after ``placeOrder`` ;
  * status events move the state (PreSubmitted/Submitted → submitted,
    ApiCancelled → cancelled, Inactive → rejected) and never un-terminalise ;
  * a bare ``Filled`` status without executions does NOT mark the row filled
    (fills do), partial fills fold cumulatively, a full fill terminalises ;
  * the orderRef shape is invisible to the reaper's structure parser.

Uses an in-memory aiosqlite DB ; ib_insync Trade/Fill objects are
SimpleNamespaces.
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from tests.fixtures.db import make_sqlite_sessionmaker as _make_session  # shared harness (#338), FKs ON

pytest.importorskip("pytest_asyncio")

pytestmark = pytest.mark.asyncio


def _trade(*, status: str = "PreSubmitted", lmt: float = 1.12325, fills=None) -> SimpleNamespace:
    return SimpleNamespace(
        order=SimpleNamespace(orderId=5, permId=422816514, lmtPrice=lmt),
        contract=SimpleNamespace(localSymbol="EUR.USD"),
        orderStatus=SimpleNamespace(status=status),
        fills=list(fills or []),
    )


def _fill(exec_id: str, qty: int, price: float, commission: float = 1.5) -> SimpleNamespace:
    return SimpleNamespace(
        execution=SimpleNamespace(execId=exec_id, shares=qty, price=price, side="SLD",
                                  time=datetime.now(UTC), exchange="IDEALPRO"),
        commissionReport=SimpleNamespace(commission=commission),
        contract=SimpleNamespace(localSymbol="EUR.USD"),
    )


async def _create(maker) -> int:
    from engines.execution.spot_orders import create_spot_order
    return await create_spot_order(
        maker, symbol="EUR", currency="USD", exchange="IDEALPRO",
        side="SELL", qty=44_513, limit_price=1.12325,
    )


async def _row(maker, sid: int):
    from persistence.models import SpotOrder
    async with maker() as db:
        return await db.get(SpotOrder, sid)


async def test_create_then_stamp_ids_and_initial_status():
    from engines.execution.spot_orders import stamp_ib_ids
    maker, engine = await _make_session()
    try:
        sid = await _create(maker)
        row = await _row(maker, sid)
        assert row.state == "pending" and row.order_type == "LMT" and row.qty == 44_513

        await stamp_ib_ids(maker, sid, _trade(status="PreSubmitted"))
        row = await _row(maker, sid)
        assert row.ib_order_id == "5"
        assert row.ib_perm_id == "422816514"
        assert row.ib_local_symbol == "EUR.USD"
        assert row.state == "submitted"  # PreSubmitted → submitted (IB holds it)
    finally:
        await engine.dispose()


async def test_market_order_keeps_ib_cap_but_never_the_unset_sentinel():
    from engines.execution.spot_orders import create_spot_order, stamp_ib_ids
    maker, engine = await _make_session()
    try:
        sid = await create_spot_order(
            maker, symbol="EUR", currency="USD", exchange="IDEALPRO",
            side="BUY", qty=100_000, limit_price=None,
        )
        assert (await _row(maker, sid)).order_type == "MKT"
        await stamp_ib_ids(maker, sid, _trade(lmt=1.7976931348623157e308))
        assert (await _row(maker, sid)).limit_price is None
        await stamp_ib_ids(maker, sid, _trade(lmt=1.1240))
        assert (await _row(maker, sid)).limit_price == pytest.approx(1.1240)
    finally:
        await engine.dispose()


async def test_status_transitions_and_terminal_guard():
    from engines.execution.spot_orders import _on_status
    maker, engine = await _make_session()
    try:
        sid = await _create(maker)
        await _on_status(_trade(status="Submitted"), sid, maker)
        assert (await _row(maker, sid)).state == "submitted"
        # A bare Filled status is NOT trusted — fills terminalise.
        await _on_status(_trade(status="Filled"), sid, maker)
        assert (await _row(maker, sid)).state == "submitted"
        await _on_status(_trade(status="ApiCancelled"), sid, maker)
        assert (await _row(maker, sid)).state == "cancelled"
        # Terminal : a late Submitted echo must not revive it.
        await _on_status(_trade(status="Submitted"), sid, maker)
        assert (await _row(maker, sid)).state == "cancelled"
    finally:
        await engine.dispose()


async def test_inactive_is_rejected_with_text():
    from engines.execution.spot_orders import _on_status
    maker, engine = await _make_session()
    try:
        sid = await _create(maker)
        await _on_status(_trade(status="Inactive"), sid, maker)
        row = await _row(maker, sid)
        assert row.state == "rejected"
        assert row.rejection_text == "Inactive"
    finally:
        await engine.dispose()


async def test_fills_fold_cumulatively_then_terminalise():
    from engines.execution.spot_orders import _on_fill, _on_status
    maker, engine = await _make_session()
    try:
        sid = await _create(maker)
        await _on_status(_trade(status="Submitted"), sid, maker)
        f1 = _fill("e1", 20_000, 1.12330)
        t = _trade(status="Submitted", fills=[f1])
        await _on_fill(t, f1, sid, maker)
        row = await _row(maker, sid)
        assert row.state == "partially_filled"
        assert row.qty_filled == 20_000
        assert row.avg_fill_price == pytest.approx(1.12330)

        f2 = _fill("e2", 24_513, 1.12320)
        t = _trade(status="Filled", fills=[f1, f2])
        await _on_fill(t, f2, sid, maker)
        row = await _row(maker, sid)
        assert row.state == "filled"
        assert row.qty_filled == 44_513
        expected = (20_000 * 1.12330 + 24_513 * 1.12320) / 44_513
        assert row.avg_fill_price == pytest.approx(expected)
        assert row.commission_usd == pytest.approx(3.0)
        assert row.filled_at is not None
        # Re-delivery of the same cumulative fill set is idempotent.
        await _on_fill(t, f2, sid, maker)
        assert (await _row(maker, sid)).qty_filled == 44_513
    finally:
        await engine.dispose()


async def test_mark_failed_when_placement_raises():
    from engines.execution.spot_orders import mark_spot_failed
    maker, engine = await _make_session()
    try:
        sid = await _create(maker)
        await mark_spot_failed(maker, sid, "Contract not qualified by IB")
        row = await _row(maker, sid)
        assert row.state == "rejected"
        assert row.rejection_text.startswith("Contract not qualified")
    finally:
        await engine.dispose()


def test_order_ref_is_invisible_to_the_structure_reaper():
    from engines.execution.reaper import parse_order_ref
    from engines.execution.spot_orders import spot_order_ref
    ref = spot_order_ref(12)
    assert ref == "fxvol:spot:12"
    assert parse_order_ref(ref) is None


def test_map_spot_status_table():
    from engines.execution.spot_orders import map_spot_status
    assert map_spot_status("PreSubmitted") == "submitted"
    assert map_spot_status("PendingCancel") == "submitted"
    assert map_spot_status("Cancelled") == "cancelled"
    assert map_spot_status("Inactive") == "rejected"
    assert map_spot_status("Filled") == "filled"
    assert map_spot_status("SomethingNew") is None
    assert map_spot_status(None) is None


# ── #376 : why a spot order waits, why it died ────────────────────────────────

def test_snap_spot_limit_onto_the_idealpro_tick_toward_the_aggressive_side():
    """Prod row S3 sent 1.12258 (off the 0.00005 grid) and IB refused it in
    188 ms with no visible reason."""
    from engines.execution.spot_orders import snap_spot_limit
    assert snap_spot_limit(1.12258, "SELL") == 1.12255   # down: still through the bid
    assert snap_spot_limit(1.12258, "BUY") == 1.12260    # up: still through the ask
    assert snap_spot_limit(1.12325, "SELL") == 1.12325   # on-grid untouched
    assert snap_spot_limit(1.12335, "BUY") == 1.12335
    assert snap_spot_limit(None, "SELL") is None


def test_ib_reason_is_read_off_the_trade_log():
    from engines.execution.spot_orders import ib_reason_from_trade, ib_why_held
    t = _trade(status="Cancelled")
    t.log = [
        SimpleNamespace(status="PendingSubmit", message="", errorCode=0),
        SimpleNamespace(status="Cancelled",
                        message="Error 110, reqId 12: The price does not conform to the minimum price variation for this contract.",
                        errorCode=110),
    ]
    assert ib_reason_from_trade(t).startswith("Error 110")
    assert ib_reason_from_trade(_trade(status="Submitted")) is None   # no log attr
    held = _trade(status="PreSubmitted")
    held.orderStatus.whyHeld = "locate"
    assert ib_why_held(held) == "locate"
    assert ib_why_held(_trade()) is None


async def test_ib_refusal_lands_as_rejected_with_ib_text_not_silent_cancel():
    from engines.execution.spot_orders import _on_status
    maker, engine = await _make_session()
    try:
        sid = await _create(maker)
        t = _trade(status="Cancelled")
        t.log = [SimpleNamespace(status="Cancelled", errorCode=110,
                                 message="Error 110, reqId 12: The price does not conform to the minimum price variation for this contract.")]
        await _on_status(t, sid, maker)
        row = await _row(maker, sid)
        assert row.state == "rejected"
        assert "minimum price variation" in row.rejection_text
        assert row.ib_status == "Cancelled"
    finally:
        await engine.dispose()


async def test_raw_ib_status_and_why_held_are_kept_while_the_order_works():
    from engines.execution.spot_orders import _on_status
    maker, engine = await _make_session()
    try:
        sid = await _create(maker)
        held = _trade(status="PreSubmitted")
        held.orderStatus.whyHeld = "locate"
        await _on_status(held, sid, maker)
        row = await _row(maker, sid)
        assert (row.state, row.ib_status, row.why_held) == ("submitted", "PreSubmitted", "locate")
        # same state, new raw status: still persisted (the blotter needs it)
        await _on_status(_trade(status="Submitted"), sid, maker)
        row = await _row(maker, sid)
        assert (row.state, row.ib_status, row.why_held) == ("submitted", "Submitted", None)
        # a user cancel (ApiCancelled, no error in the log) stays "cancelled"
        await _on_status(_trade(status="ApiCancelled"), sid, maker)
        assert (await _row(maker, sid)).state == "cancelled"
    finally:
        await engine.dispose()

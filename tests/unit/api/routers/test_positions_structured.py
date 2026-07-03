"""GET /api/v1/positions/structured — the booking-driven, grouped positions view.

Verifies: structures come from trade_structure (real structure_type + tenor), legs
from trade_order, live marks attach from open_position (by ib_local_symbol or the
trade_id FK), net greeks sum the linked legs, and IB rows tied to no booked leg fall
into ``unlinked``.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

pytest.importorskip("pytest_asyncio")
pytestmark = pytest.mark.asyncio


def _coerce_bigint_to_integer(metadata) -> None:
    from sqlalchemy import BigInteger, Integer
    for table in metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, BigInteger):
                col.type = Integer()


async def _make_session():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from persistence.models import Base

    _coerce_bigint_to_integer(Base.metadata)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False), engine


def _pos(**kw):
    from persistence.models import OpenPosition
    base = dict(side="BUY", quantity=25, entry_timestamp=datetime.now(UTC))
    base.update(kw)
    return OpenPosition(**base)


async def test_structured_groups_by_booking_and_buckets_unlinked():
    from api.routers.positions import list_structured
    from persistence.models import StructureOrder, TradeStructure

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            s = TradeStructure(
                structure_type="risk_reversal", product_label="Risk Reversal",
                reference_tenor="3M", base_qty=25, state="fully_filled",
            )
            db.add(s)
            await db.flush()  # assign s.id
            db.add_all([
                StructureOrder(structure_id=s.id, leg_idx=0, contract_type="call", side="BUY",
                               qty=25, state="filled", ib_local_symbol="EUUV6 C1087"),
                StructureOrder(structure_id=s.id, leg_idx=1, contract_type="put", side="SELL",
                               qty=25, state="filled", ib_local_symbol="EUUV6 P1087"),
            ])
            db.add_all([
                # linked via the trade_id FK
                _pos(structure="EUUV6 C1087", side="BUY", trade_id=s.id, delta_usd=100,
                     current_pnl_usd=10, product_label="Vanilla Call", tenor="3M"),
                # linked via the leg's ib_local_symbol (no FK stamp)
                _pos(structure="EUUV6 P1087", side="SELL", delta_usd=-50,
                     current_pnl_usd=5, product_label="Vanilla Put", tenor="3M"),
                # unrelated IB-account position — not part of any booked structure
                _pos(structure="EUUU6 P1145", side="SELL", trade_id=None,
                     product_label="Vanilla Put", tenor="2M", delta_usd=999),
            ])
            await db.commit()

            out = await list_structured(db, limit=50)

        assert len(out["structures"]) == 1
        st = out["structures"][0]
        assert st["structure_type"] == "risk_reversal"
        assert st["tenor"] == "3M"                       # traded tenor, not re-bucketed
        assert len(st["legs"]) == 2
        assert {lg["side"] for lg in st["legs"]} == {"BUY", "SELL"}
        assert st["net"]["delta_usd"] == 50.0            # 100 + (-50)
        assert st["net"]["n_linked"] == 2

        assert len(out["unlinked"]) == 1
        assert out["unlinked"][0]["symbol"] == "EUUU6 P1145"
        assert out["unlinked"][0]["delta_usd"] == 999.0
    finally:
        await engine.dispose()


async def test_structured_excludes_closed_structures():
    from api.routers.positions import list_structured
    from persistence.models import TradeStructure

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            db.add(TradeStructure(structure_type="straddle_atm", reference_tenor="1M",
                                  base_qty=10, state="closed"))
            await db.commit()
            out = await list_structured(db, limit=50)
        assert out["structures"] == []
    finally:
        await engine.dispose()


async def test_structured_excludes_submitted_but_unfilled_structure():
    """A structure whose legs never filled (no linked open_position) is an order,
    not a position — it must NOT appear in the grouped Open positions view."""
    from api.routers.positions import list_structured
    from persistence.models import StructureOrder, TradeStructure

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            s = TradeStructure(
                structure_type="strangle", product_label="Strangle",
                reference_tenor="2M", base_qty=10, state="submitted",
            )
            db.add(s)
            await db.flush()
            db.add_all([
                StructureOrder(structure_id=s.id, leg_idx=0, contract_type="call", side="BUY",
                               qty=10, state="submitted", ib_local_symbol=None),
                StructureOrder(structure_id=s.id, leg_idx=1, contract_type="put", side="BUY",
                               qty=10, state="pending", ib_local_symbol=None),
            ])
            # no open_position rows → nothing is actually open
            await db.commit()
            out = await list_structured(db, limit=50)
        assert out["structures"] == []       # pending order, excluded from positions
        assert out["unlinked"] == []
    finally:
        await engine.dispose()


async def test_shared_contract_does_not_cross_link():
    """Two structures use the SAME contract (EUUQ6 C1145) ; IB holds one netted
    position, attributed by trade_id to B. A must NOT claim B's fill as its own."""
    from api.routers.positions import list_structured
    from persistence.models import StructureOrder, TradeStructure

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            a = TradeStructure(structure_type="butterfly", reference_tenor="1M",
                               base_qty=25, state="partial_fill")
            b = TradeStructure(structure_type="call spread", reference_tenor="1M",
                               base_qty=25, state="fully_filled")
            db.add_all([a, b])
            await db.flush()
            db.add_all([
                StructureOrder(structure_id=a.id, leg_idx=0, contract_type="call", side="BUY",
                               qty=25, state="filled", ib_local_symbol="EUUQ6 C1130"),
                StructureOrder(structure_id=a.id, leg_idx=1, contract_type="call", side="SELL",
                               qty=50, state="submitted", ib_local_symbol="EUUQ6 C1145"),  # shared, unfilled for A
                StructureOrder(structure_id=b.id, leg_idx=0, contract_type="call", side="SELL",
                               qty=25, state="filled", ib_local_symbol="EUUQ6 C1145"),
            ])
            db.add_all([
                _pos(structure="EUUQ6 C1130", side="BUY", trade_id=a.id, delta_usd=100),
                _pos(structure="EUUQ6 C1145", side="SELL", trade_id=b.id, delta_usd=-100),
            ])
            await db.commit()
            out = await list_structured(db, limit=50)

        by_id = {s["structure_id"]: s for s in out["structures"]}
        a_legs = {lg["leg_idx"]: lg for lg in by_id[a.id]["legs"]}
        assert a_legs[0]["linked"] is True    # A owns C1130
        assert a_legs[1]["linked"] is False   # C1145 belongs to B, not A
        assert by_id[a.id]["net"]["n_linked"] == 1
        assert by_id[b.id]["legs"][0]["linked"] is True
    finally:
        await engine.dispose()


async def test_structured_keeps_half_filled_as_naked_residual():
    """Half-filled RR: put filled (1 linked open position), call not. It IS an open
    position (a naked short put) so it must still show — grouped, with the leg states."""
    from api.routers.positions import list_structured
    from persistence.models import StructureOrder, TradeStructure

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            s = TradeStructure(
                structure_type="risk_reversal", product_label="Risk Reversal",
                reference_tenor="3M", base_qty=25, state="partial_fill",
            )
            db.add(s)
            await db.flush()
            db.add_all([
                StructureOrder(structure_id=s.id, leg_idx=0, contract_type="call", side="BUY",
                               qty=25, state="submitted", ib_local_symbol=None),
                StructureOrder(structure_id=s.id, leg_idx=1, contract_type="put", side="SELL",
                               qty=25, state="filled", ib_local_symbol="EUUV6 P1087"),
            ])
            db.add(_pos(structure="EUUV6 P1087", side="SELL", delta_usd=-50,
                        current_pnl_usd=5, product_label="Vanilla Put", tenor="3M"))
            await db.commit()
            out = await list_structured(db, limit=50)
        assert len(out["structures"]) == 1
        st = out["structures"][0]
        assert st["net"]["n_linked"] == 1
        states = {lg["side"]: lg["state"] for lg in st["legs"]}
        assert states == {"BUY": "submitted", "SELL": "filled"}
    finally:
        await engine.dispose()


# ── GET /positions/reconciliation — book vs broker breaks ────────────────────

async def test_compute_breaks_classifies_each_kind():
    """Pure diff logic (no DB) ; async only to match the module's asyncio mark."""
    from api.routers.positions import _compute_breaks

    expected = {
        "OK":      5.0,    # book +5, IB +5  → no break
        "MISS":    3.0,    # book +3, IB flat → missing_at_ib
        "QTY":     10.0,   # book +10, IB +6  → quantity (short 4 at IB)
        "DIR":     4.0,    # book long, IB short → direction
        "NOISE":   1.00004,  # within eps of actual → not a break
    }
    actual = {
        "OK":       5.0,
        "QTY":      6.0,
        "DIR":     -4.0,
        "NOISE":    1.0,
        "ORPHAN":   2.0,   # IB holds it, book has no record → unbooked_at_ib
    }
    struct = {"OK": 1, "MISS": 2, "QTY": 3, "DIR": 4, "ORPHAN": None}
    by_contract = {b["contract"]: b for b in _compute_breaks(expected, actual, struct)}

    assert "OK" not in by_contract and "NOISE" not in by_contract
    assert by_contract["MISS"]["kind"] == "missing_at_ib"
    assert by_contract["QTY"]["kind"] == "quantity" and by_contract["QTY"]["break"] == 4.0
    assert by_contract["DIR"]["kind"] == "direction"
    assert by_contract["ORPHAN"]["kind"] == "unbooked_at_ib"
    assert by_contract["ORPHAN"]["structure_id"] is None
    assert by_contract["MISS"]["structure_id"] == 2


async def test_reconciliation_endpoint_flags_a_quantity_break():
    """End-to-end: filled orders (book) vs IB mirror. A leg IB under-holds is a break;
    a leg that matches is clean; an IB row with no order is unbooked."""
    from api.routers.positions import reconciliation
    from persistence.models import StructureOrder, TradeStructure

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            s = TradeStructure(structure_type="strangle", reference_tenor="3M",
                               base_qty=10, state="partial_fill")
            db.add(s)
            await db.flush()
            db.add_all([
                # book: long 10 of the call (fully filled) — IB holds 10 → clean
                StructureOrder(structure_id=s.id, leg_idx=0, contract_type="call", side="BUY",
                               qty=10, qty_filled=10, state="filled", ib_local_symbol="EUUV6 C1130"),
                # book: long 10 of the put (filled) — IB holds only 6 → break of +4
                StructureOrder(structure_id=s.id, leg_idx=1, contract_type="put", side="BUY",
                               qty=10, qty_filled=10, state="filled", ib_local_symbol="EUUV6 P1090"),
            ])
            db.add_all([
                _pos(structure="EUUV6 C1130", side="BUY", quantity=10, trade_id=s.id),
                _pos(structure="EUUV6 P1090", side="BUY", quantity=6, trade_id=s.id),
                _pos(structure="EUUU6 C1200", side="SELL", quantity=3, trade_id=None),  # orphan
            ])
            await db.commit()
            out = await reconciliation(db)

        by = {b["contract"]: b for b in out["breaks"]}
        assert "EUUV6 C1130" not in by                 # matched → no break
        assert by["EUUV6 P1090"]["kind"] == "quantity"
        assert by["EUUV6 P1090"]["break"] == 4.0       # +10 book − (+6) IB
        assert by["EUUV6 P1090"]["structure_id"] == s.id
        assert by["EUUU6 C1200"]["kind"] == "unbooked_at_ib"  # IB holds, no order
        assert out["n_breaks"] == 2
    finally:
        await engine.dispose()

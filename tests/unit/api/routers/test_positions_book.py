"""GET /api/v1/positions/book — the panel read of the forward book (I7).

Holdings come from ``leg_position`` (the fold of each leg's own fills) ; the
IB mirror contributes marks/greeks only. Also covers the close-attribution
stamping (``closes_order_id``) resolved at close creation.
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


async def _seed_leg_with_position(db, *, qty: float = 10, side: str = "BUY",
                                  local_symbol: str = "EUUV6 C1130",
                                  avg_price: float = 1.20,
                                  reserved: float = 0.0):
    from persistence.models import LegPosition, StructureOrder, TradeStructure
    s = TradeStructure(structure_type="strangle", product_label="Strangle 3M",
                       reference_tenor="3M", base_qty=int(abs(qty)),
                       state="fully_filled", execution_mode="live")
    db.add(s)
    await db.flush()
    o = StructureOrder(
        structure_id=s.id, leg_idx=0, order_role="entry",
        contract_type="call", contract_strike=1.13, side=side,
        qty=int(abs(qty)), order_type="LMT", state="filled",
        ib_local_symbol=local_symbol,
    )
    db.add(o)
    await db.flush()
    signed = qty if side == "BUY" else -qty
    db.add(LegPosition(order_id=o.id, open_qty=signed, reserved_qty=reserved,
                       avg_price=avg_price))
    return s.id, o.id


async def test_book_shows_holdings_when_mirror_is_empty():
    """Sync lag / feed flap : the mirror is empty but the book knows the
    holding — the panel must still see it (I7)."""
    from api.routers.positions import list_book

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            sid, oid = await _seed_leg_with_position(db)
            await db.commit()
            out = await list_book(db)
        assert len(out) == 1
        row = out[0]
        assert row["contract"] == "EUUV6 C1130"
        assert row["open_qty"] == 10
        assert row["quantity"] == 10 and row["side"] == "BUY"
        assert row["trade_id"] == sid and row["order_id"] == oid
        assert row["available"] == 10
        assert row["market_price"] is None       # no mirror → no mark, still listed
    finally:
        await engine.dispose()


async def test_book_enriches_marks_from_mirror_but_not_identity():
    """A matching mirror row supplies the mark (pricing enrichment) ; the
    quantity/attribution stay the book's own."""
    from api.routers.positions import list_book
    from persistence.models import OpenPosition

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            _sid, _oid = await _seed_leg_with_position(db, qty=10, avg_price=1.20)
            db.add(OpenPosition(
                structure="EUUV6 C1130", side="BUY", quantity=6,   # mirror lags !
                market_price=1.30, entry_timestamp=datetime.now(UTC),
            ))
            await db.commit()
            out = await list_book(db)
        row = out[0]
        assert row["quantity"] == 10                 # book qty, NOT the mirror's 6
        assert row["market_price"] == pytest.approx(1.30)
        # unreal = (mark − avg) · open_qty · multiplier(EUR = 125k)
        assert row["current_pnl_usd"] == pytest.approx((1.30 - 1.20) * 10 * 125_000)
    finally:
        await engine.dispose()


async def test_flat_unencumbered_legs_are_not_listed():
    from api.routers.positions import list_book

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            await _seed_leg_with_position(db, qty=0, side="BUY")
            await db.commit()
            out = await list_book(db)
        assert out == []
    finally:
        await engine.dispose()


async def test_close_stamps_closes_order_id_from_trade_and_contract():
    """Forward attribution at close creation : the closing order points at
    the entry leg resolved from the position's trade + contract."""
    from api.routers.positions import _resolve_entry_leg_id
    from persistence.models import OpenPosition

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            sid, oid = await _seed_leg_with_position(db)
            pos = OpenPosition(structure="EUUV6 C1130", side="BUY", quantity=10,
                               trade_id=sid, entry_timestamp=datetime.now(UTC))
            db.add(pos)
            await db.commit()
            assert await _resolve_entry_leg_id(db, pos, None) == oid

            orphan = OpenPosition(structure="EUUU6 C1200", side="BUY", quantity=3,
                                  trade_id=None, entry_timestamp=datetime.now(UTC))
            db.add(orphan)
            await db.commit()
            assert await _resolve_entry_leg_id(db, orphan, None) is None

            assert await _resolve_entry_leg_id(db, pos, oid) == oid
    finally:
        await engine.dispose()

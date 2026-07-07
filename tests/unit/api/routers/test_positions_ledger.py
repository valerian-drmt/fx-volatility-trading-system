"""GET /api/v1/positions/ledger — positions + P&L folded from the trade_fill log.

Verifies the endpoint reads fills (joined to their order for the contract +
multiplier), folds them in execution order via ``core.ledger`` (average-cost),
and returns realised P&L net of commissions + totals.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

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


async def test_ledger_folds_fills_into_realized_pnl():
    """BUY-to-open then SELL-to-close the same contract (two orders) → flat, with
    realised P&L = premium diff × qty × multiplier − commissions."""
    from api.routers.positions import ledger
    from persistence.models import StructureFill, StructureOrder, TradeStructure
    from shared.contracts import multiplier_for

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            s = TradeStructure(structure_type="vanilla_call", reference_tenor="3M",
                               base_qty=10, state="fully_filled")
            db.add(s)
            await db.flush()
            entry = StructureOrder(structure_id=s.id, leg_idx=0, order_role="entry",
                                   contract_type="call", side="BUY", qty=10, state="filled",
                                   ib_local_symbol="EUUV6 C1130", contract_symbol="EUR")
            close = StructureOrder(structure_id=s.id, leg_idx=1, order_role="closing",
                                   contract_type="call", side="SELL", qty=10, state="filled",
                                   ib_local_symbol="EUUV6 C1130", contract_symbol="EUR")
            db.add_all([entry, close])
            await db.flush()
            t0 = datetime.now(UTC)
            db.add_all([
                StructureFill(order_id=entry.id, ib_execution_id="e1", timestamp=t0,
                              qty_filled=10, fill_price=0.02, commission_usd=5.0, side="BUY"),
                StructureFill(order_id=close.id, ib_execution_id="e2", timestamp=t0 + timedelta(seconds=1),
                              qty_filled=10, fill_price=0.03, commission_usd=5.0, side="SELL"),
            ])
            await db.commit()
            out = await ledger(db)

        p = {x["contract"]: x for x in out["positions"]}["EUUV6 C1130"]
        mult = multiplier_for("EUR")
        assert p["net_qty"] == 0.0
        assert p["realized_pnl"] == pytest.approx((0.03 - 0.02) * 10 * mult - 10.0, abs=0.01)
        assert out["totals"]["commission"] == pytest.approx(10.0)
        assert out["totals"]["realized_pnl"] == pytest.approx((0.03 - 0.02) * 10 * mult - 10.0, abs=0.01)
    finally:
        await engine.dispose()


async def test_ledger_reports_open_position_and_unrealized():
    """A still-open long shows net_qty + avg_cost and unrealised MTM off the mirror mark."""
    from api.routers.positions import ledger
    from persistence.models import OpenPosition, StructureFill, StructureOrder, TradeStructure
    from shared.contracts import multiplier_for

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            s = TradeStructure(structure_type="vanilla_call", reference_tenor="3M",
                               base_qty=10, state="fully_filled")
            db.add(s)
            await db.flush()
            o = StructureOrder(structure_id=s.id, leg_idx=0, order_role="entry",
                               contract_type="call", side="BUY", qty=10, state="filled",
                               ib_local_symbol="EUUV6 C1130", contract_symbol="EUR")
            db.add(o)
            await db.flush()
            db.add(StructureFill(order_id=o.id, ib_execution_id="e1", timestamp=datetime.now(UTC),
                                 qty_filled=10, fill_price=0.02, commission_usd=0.0, side="BUY"))
            db.add(OpenPosition(structure="EUUV6 C1130", side="BUY", quantity=10,
                                market_price=0.03, entry_timestamp=datetime.now(UTC)))
            await db.commit()
            out = await ledger(db)

        p = {x["contract"]: x for x in out["positions"]}["EUUV6 C1130"]
        mult = multiplier_for("EUR")
        assert p["net_qty"] == 10.0
        assert p["avg_cost"] == pytest.approx(0.02)
        assert p["realized_pnl"] == 0.0
        assert p["unrealized_pnl"] == pytest.approx((0.03 - 0.02) * 10 * mult)  # mark 0.03 vs cost 0.02
    finally:
        await engine.dispose()

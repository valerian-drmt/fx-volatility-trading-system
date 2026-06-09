"""Unit tests for engines.execution.position_close_finaliser."""
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


async def test_finalise_closes_position_and_computes_pnl():
    from engines.execution.position_close_finaliser import finalise_position_close
    from persistence.models import (
        ExitAlert,
        HedgeOrder,
        TradePosition,
        TradeStructure,
    )

    maker, engine = await _make_session()
    try:
        now = datetime.now(UTC)
        async with maker() as db:
            # Entry structure + open position (paid 100, costs 5)
            entry = TradeStructure(
                structure_type="straddle", reference_tenor="3M",
                base_qty=1, state="fully_filled", execution_mode="live",
                total_premium_paid_usd=100.0, total_entry_cost_usd=5.0,
            )
            db.add(entry)
            await db.flush()
            pos = TradePosition(
                structure_id=entry.id, opened_at=now,
                entry_premium_usd=100.0, entry_total_cost_usd=5.0,
                state="closing",
            )
            db.add(pos)
            await db.flush()
            # Closing structure : exit_premium_usd=130, cost=5
            closing = TradeStructure(
                structure_type="straddle", reference_tenor="3M",
                base_qty=1, state="fully_filled", execution_mode="live",
                total_premium_paid_usd=-130.0,    # opposite-side : negative
                total_entry_cost_usd=5.0,
            )
            db.add(closing)
            await db.flush()
            db.add(ExitAlert(
                position_id=pos.id, timestamp=now,
                rule_triggered="signal_reverse", action_recommended="EXIT",
                priority=1, rule_detail={}, auto_executed=True,
                execution_status="in_progress", closing_structure_id=closing.id,
            ))
            db.add(HedgeOrder(
                position_id=pos.id, triggered_at=now,
                delta_imbalance_at_trigger=0.5, rebalance_threshold_used=0.05,
                hedge_qty=1, side="SELL", state="filled",
                total_cost_usd=2.0,
            ))
            await db.commit()
            closing_id = closing.id
            pos_id = pos.id

        ok = await finalise_position_close(
            sessionmaker_factory=maker, closing_structure_id=closing_id,
        )
        assert ok is True
        async with maker() as db:
            pos = await db.get(TradePosition, pos_id)
        assert pos.state == "closed"
        assert pos.closed_at is not None
        # gross = -(-130) - 100 = 30
        assert pos.gross_pnl_usd == pytest.approx(30.0)
        # net = 30 - 5 - 5 - 2 = 18
        assert pos.net_pnl_usd == pytest.approx(18.0)
    finally:
        await engine.dispose()


async def test_finalise_idempotent_on_already_closed():
    from engines.execution.position_close_finaliser import finalise_position_close

    maker, engine = await _make_session()
    try:
        # No data → returns False (no closing structure with that id).
        ok = await finalise_position_close(
            sessionmaker_factory=maker, closing_structure_id=999999,
        )
        assert ok is False
    finally:
        await engine.dispose()

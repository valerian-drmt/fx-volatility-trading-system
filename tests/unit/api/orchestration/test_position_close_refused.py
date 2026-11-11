"""A close the execution engine refuses before placing anything (423 kill
switch, 503 not ready, unreachable) must not leave the position stuck in
`closing`: the exit monitor only selects `open` positions, so nothing would
retry it."""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from tests.fixtures.db import make_sqlite_sessionmaker as _make_session

pytest.importorskip("pytest_asyncio")

pytestmark = pytest.mark.asyncio


async def test_a_refused_close_reopens_the_position_and_fails_the_closing_legs() -> None:
    from api.orchestration.position_close import _revert_refused_close
    from persistence.models import BookedPosition, StructureOrder, TradeEvent, TradeStructure

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            entry = TradeStructure(structure_type="straddle", reference_tenor="3M", base_qty=1,
                                   state="fully_filled", execution_mode="live")
            closing = TradeStructure(structure_type="straddle", reference_tenor="3M", base_qty=1,
                                     state="submitted", execution_mode="live")
            db.add_all([entry, closing])
            await db.flush()
            entry_leg = StructureOrder(structure_id=entry.id, leg_idx=0, order_role="entry", contract_type="call",
                                       contract_strike=1.16, contract_expiry=date(2026, 12, 18), side="BUY", qty=1,
                                       order_type="LMT", limit_price=0.01, state="filled")
            db.add(entry_leg)
            await db.flush()
            db.add(StructureOrder(structure_id=closing.id, leg_idx=0, order_role="closing", contract_type="call",
                                  contract_strike=1.16, contract_expiry=date(2026, 12, 18), side="SELL", qty=1,
                                  order_type="LMT", limit_price=0.01, state="pending", closes_order_id=entry_leg.id))
            pos = BookedPosition(structure_id=entry.id, opened_at=datetime.now(UTC), entry_premium_usd=100.0,
                                 entry_total_cost_usd=5.0, state="closing")
            db.add(pos)
            await db.commit()
            pid, sid = int(pos.id), int(closing.id)

        await _revert_refused_close(maker, pid, sid, None, {"status": 423})

        async with maker() as db:
            assert (await db.get(BookedPosition, pid)).state == "open"
            assert (await db.get(TradeStructure, sid)).state == "fully_failed"
            from sqlalchemy import select
            legs = (await db.execute(select(StructureOrder).where(StructureOrder.structure_id == sid))).scalars().all()
            assert [o.state for o in legs] == ["rejected"]
            events = (await db.execute(select(TradeEvent.event_type))).scalars().all()
            assert "position_close_refused" in events
    finally:
        await engine.dispose()

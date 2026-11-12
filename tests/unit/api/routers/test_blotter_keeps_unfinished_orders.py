"""The Orders blotter always carries the orders still in flight.

The endpoint returned the N most recent structures only: an order still
waiting at IB dropped out of the blotter once N newer trades existed. The
unfinished ones are now always returned (newest first, as stored); the
screen pins them at the top. Nothing is reordered in the database.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from api.routers.trade import list_submitted_structures
from persistence.models import StructureOrder, TradeStructure

pytestmark = pytest.mark.unit

T0 = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)


async def _trade(db: Any, minutes_ago: int, order_state: str) -> int:
    s = TradeStructure(structure_type="vanilla_call", reference_tenor="1M", base_qty=1,
                       state="submitted", execution_mode="live", created_at=T0 - timedelta(minutes=minutes_ago))
    db.add(s)
    await db.flush()
    db.add(StructureOrder(structure_id=s.id, leg_idx=0, order_role="closing", contract_type="call", side="SELL",
                          qty=1, order_type="LMT", state=order_state, ib_local_symbol="EUUZ6 C1210"))
    await db.flush()
    return int(s.id)


async def test_an_old_order_still_in_flight_stays_in_the_blotter(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        old_waiting = await _trade(db, 600, "submitted")
        old_done = await _trade(db, 500, "filled")
        recent = [await _trade(db, m, "filled") for m in (3, 2, 1)]
        await db.commit()

        out = await list_submitted_structures(db, limit=3)
        ids = [r["id"] for r in out]
        assert ids[:3] == recent[::-1], "the window is still the newest first, as stored"
        assert old_waiting in ids, "an unfinished order is never pushed out by newer trades"
        assert old_done not in ids
        assert len(ids) == 4

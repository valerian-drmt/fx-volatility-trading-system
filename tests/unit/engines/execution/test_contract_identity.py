"""Every order row carries its contract from the moment it is placed.

Prod, 2026-11-13, the order rows of one afternoon:

* working orders had no local symbol and no conId (written on the first
  fill only);
* an entry on EUUG7 carried ``2027-02-11`` as its expiry (today + 3M, the
  tenor's calendar date) while a close of the same contract carried
  ``2027-02-05`` (the contract's own);
* the hedge future had no expiry at all.

One rule for every product and every path: what IB's qualified contract
says is written on the row at placement.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from engines.execution.placement import contract_identity, stamp_placement
from persistence.models import Instrument, StructureOrder, TradeStructure

pytestmark = pytest.mark.unit


def _contract(**over: Any) -> SimpleNamespace:
    base = dict(conId=853091487, localSymbol="EUUG7 P1120", secType="FOP", symbol="EUR", currency="USD",
                exchange="CME", multiplier="125000", lastTradeDateOrContractMonth="20270205", strike=1.12, right="P")
    base.update(over)
    return SimpleNamespace(**base)


def test_an_option_and_a_future_are_read_the_same_way() -> None:
    assert contract_identity(_contract()) == {
        "ib_local_symbol": "EUUG7 P1120", "con_id": 853091487, "contract_expiry": date(2027, 2, 5),
    }
    fut = _contract(conId=550123082, localSymbol="6EH7", secType="FUT", lastTradeDateOrContractMonth="20270315",
                    strike=0.0, right="")
    assert contract_identity(fut) == {
        "ib_local_symbol": "6EH7", "con_id": 550123082, "contract_expiry": date(2027, 3, 15),
    }


def test_nothing_is_invented() -> None:
    assert contract_identity(None) == {}
    assert contract_identity(_contract(conId=0)) == {}, "not qualified"
    assert contract_identity(_contract(secType="BAG", localSymbol="EUR")) == {}, "a combo is not a leg's contract"
    # a contract month without a day is not an expiry date
    assert "contract_expiry" not in contract_identity(_contract(lastTradeDateOrContractMonth="202702"))


async def test_the_row_carries_the_contract_as_soon_as_it_is_placed(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        s = TradeStructure(structure_type="long put", reference_tenor="3M", base_qty=50, state="submitted",
                           execution_mode="live")
        db.add(s)
        await db.flush()
        o = StructureOrder(structure_id=s.id, leg_idx=0, order_role="entry", contract_type="put", side="BUY", qty=50,
                           order_type="LMT", limit_price=0.0179, state="pending",
                           contract_expiry=date(2027, 2, 11))   # the tenor's date, as the preview wrote it
        db.add(o)
        await db.commit()
        oid = int(o.id)

    trade = SimpleNamespace(order=SimpleNamespace(orderId=116, permId=9001), contract=_contract())
    await stamp_placement(sqlite_sessionmaker, StructureOrder, oid, trade)

    async with sqlite_sessionmaker() as db:
        row = await db.get(StructureOrder, oid)
        assert (row.state, row.ib_order_id, row.qty_filled or 0) == ("submitted", "116", 0)
        assert (row.ib_local_symbol, row.con_id, row.contract_expiry) == ("EUUG7 P1120", 853091487, date(2027, 2, 5))
        assert (await db.get(Instrument, 853091487)).local_symbol == "EUUG7 P1120"

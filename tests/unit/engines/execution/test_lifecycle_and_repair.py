"""Contract lifecycle (#463), booked-trade check (#464), order repair (#465),
instrument identity (#460).
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select

from core.contracts import contract_expiry, contract_month_end
from engines.execution.lifecycle import expire_legs, lifecycle_pass
from engines.execution.position_sync import _build_leg_to_trade_map, reconcile_trade_positions
from persistence.instruments import (
    expiries_by_symbol,
    instrument_from_ib_position,
    upsert_instruments,
)
from persistence.models import (
    BookedPosition,
    Instrument,
    LegPosition,
    OpenPosition,
    PositionEvent,
    StructureFill,
    StructureOrder,
    TradeStructure,
)
from persistence.position_events import record_repair
from persistence.projection import rebuild_leg

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
TODAY = NOW.date()


class _Executor:
    def __init__(self, positions: list[dict] | None = None, reporting: bool = True) -> None:
        self._positions = positions or []
        self._reporting = reporting

    def is_connected(self) -> bool:
        return True

    def account_is_reporting(self) -> bool:
        return self._reporting

    async def list_positions(self) -> list[dict]:
        return list(self._positions)


async def _leg(
    db: Any, symbol: str, qty: int, *, side: str = "BUY", ctype: str = "call", state: str = "fully_filled",
    strike: float | None = None, expiry: date | None = None, fill: bool = True,
) -> tuple[TradeStructure, StructureOrder]:
    s = TradeStructure(structure_type="vanilla", reference_tenor="1M", base_qty=qty, state=state, execution_mode="live")
    db.add(s)
    await db.flush()
    o = StructureOrder(
        structure_id=s.id, leg_idx=0, order_role="entry", contract_type=ctype, side=side, qty=qty,
        order_type="LMT", state="filled", ib_local_symbol=symbol, qty_filled=qty,
        contract_strike=strike, contract_expiry=expiry, avg_fill_price=0.01,
    )
    db.add(o)
    await db.flush()
    if fill:
        db.add(StructureFill(order_id=o.id, ib_execution_id=f"e{o.id}", timestamp=NOW, qty_filled=qty,
                             fill_price=0.01, commission_usd=0.0, side=side))
        await db.flush()
        await rebuild_leg(db, order_id=o.id)
        await db.flush()
    return s, o


async def _open_qty(db: Any, order_id: int) -> float:
    lp = (await db.execute(select(LegPosition).where(LegPosition.order_id == order_id))).scalar_one_or_none()
    return float(lp.open_qty) if lp is not None else 0.0


# ── expiry dates ─────────────────────────────────────────────────────────────

def test_contract_month_end_from_the_ib_symbol() -> None:
    assert contract_month_end("EUUZ6 P1075", TODAY) == date(2026, 12, 31)
    assert contract_month_end("EUUQ6 C1155", TODAY) == date(2026, 8, 31)
    assert contract_month_end("6EH7", TODAY) == date(2027, 3, 31)
    assert contract_month_end("M6EU6", TODAY) == date(2026, 9, 30)
    assert contract_month_end("EUR.USD", TODAY) is None


def test_rule_expiry_matches_what_ib_reports_for_the_contracts_held() -> None:
    from core.contracts import rule_expiry

    # prod open_position.expiry on 2026-11-12
    assert rule_expiry("EUUZ6 C1210", TODAY) == date(2026, 12, 4)
    assert rule_expiry("EUUG7 P1120", TODAY) == date(2027, 2, 5)
    assert rule_expiry("6EH7", TODAY) == date(2027, 3, 15)
    assert rule_expiry("EUUX6 C1115", TODAY) == date(2026, 11, 6)
    assert rule_expiry("6EZ6", TODAY) == date(2026, 12, 14)
    assert rule_expiry("EUR.USD", TODAY) is None


def test_listed_expiry_wins_over_the_rule() -> None:
    assert contract_expiry("EUUZ6 P1075", date(2026, 12, 3), TODAY) == date(2026, 12, 3)
    assert contract_expiry("EUUZ6 P1075", None, TODAY) == date(2026, 12, 4)


# ── lifecycle ────────────────────────────────────────────────────────────────

async def test_expired_leg_the_broker_dropped_is_closed_with_an_event(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        s, o = await _leg(db, "EUUQ6 C1155", 100)            # expired in August
        _, live = await _leg(db, "EUUG7 C1110", 10)            # February 2027: alive
        db.add(BookedPosition(structure_id=s.id, opened_at=NOW, entry_premium_usd=1.0,
                              entry_total_cost_usd=0.0, state="open"))
        await db.flush()
        out = await expire_legs(db, today=TODAY, now=NOW)
        await db.flush()
        assert out["expired_legs"] == [o.id] and out["closed_trades"] == [s.id]
        assert await _open_qty(db, o.id) == 0 and await _open_qty(db, live.id) == 10
        ev = (await db.execute(select(PositionEvent))).scalars().all()
        assert len(ev) == 1 and ev[0].kind == "expiry" and ev[0].side == "SELL" and float(ev[0].qty) == 100
        assert (await db.get(TradeStructure, s.id)).state == "closed"
        pos = (await db.execute(select(BookedPosition))).scalar_one()
        assert pos.state == "expired" and pos.close_reason == "expired"
        # idempotent
        again = await expire_legs(db, today=TODAY, now=NOW)
        assert again["expired_legs"] == []


async def test_expired_leg_still_at_the_broker_is_left_alone(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        _, o = await _leg(db, "EUUQ6 C1155", 100)
        db.add(OpenPosition(structure="EUUQ6 C1155", side="BUY", quantity=Decimal(100), entry_timestamp=NOW))
        await db.flush()
        out = await expire_legs(db, today=TODAY, now=NOW)
        assert out["expired_legs"] == [] and out["expired_but_held"] == ["EUUQ6 C1155"]
        assert await _open_qty(db, o.id) == 100


async def test_an_option_expired_this_month_is_swept_without_waiting_for_the_month_end(sqlite_sessionmaker: Any) -> None:
    """Prod, 2026-11-12: six EUUX6 legs (expired on the 6th) stayed "book
    only" because the fallback waited for 1 December."""
    async with sqlite_sessionmaker() as db:
        _, o = await _leg(db, "EUUX6 C1115", 10)
        _, later = await _leg(db, "EUUZ6 C1210", 10)        # expires 4 December: untouched
        out = await expire_legs(db, today=TODAY, now=NOW)
        assert out["expired_legs"] == [o.id]
        assert await _open_qty(db, later.id) == 10


async def test_the_listed_expiry_is_recorded_from_the_broker(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        _, o = await _leg(db, "EUUX6 C1115", 10)
        await upsert_instruments(db, [instrument_from_ib_position({
            "con_id": 777, "local_symbol": "EUUX6 C1115", "sec_type": "FOP", "symbol": "EUR",
            "expiry": "20261106", "strike": 1.115, "right": "C", "multiplier": "125000",
        })])
        assert await expiries_by_symbol(db, ["EUUX6 C1115"]) == {"EUUX6 C1115": date(2026, 11, 6)}
        assert (await expire_legs(db, today=TODAY, now=NOW))["expired_legs"] == [o.id]


async def test_lifecycle_does_nothing_on_a_dead_feed(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        _, o = await _leg(db, "EUUQ6 C1155", 100)
        await db.commit()
    out = await lifecycle_pass(sqlite_sessionmaker, _Executor(reporting=False), now=NOW)
    assert out == {"skipped": "account_not_reporting"}
    async with sqlite_sessionmaker() as db:
        assert await _open_qty(db, o.id) == 100


# ── instrument ───────────────────────────────────────────────────────────────

async def test_instrument_upsert_inserts_once_and_keeps_richer_fields(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        spot = instrument_from_ib_position({"con_id": 12087792, "local_symbol": "EUR.USD", "sec_type": "CASH",
                                            "symbol": "EUR", "currency": "USD", "multiplier": None})
        fop = instrument_from_ib_position({"con_id": 5, "local_symbol": "EUUZ6 C1210", "sec_type": "FOP",
                                           "symbol": "EUR", "expiry": "20261204", "strike": 1.21, "right": "C",
                                           "multiplier": "125000"})
        assert await upsert_instruments(db, [spot, fop, None]) == 2
        assert await upsert_instruments(db, [spot, fop]) == 0, "unchanged contracts cost nothing"
        rows = {r.local_symbol: r for r in (await db.execute(select(Instrument))).scalars().all()}
        assert float(rows["EUR.USD"].multiplier) == 1.0 and rows["EUR.USD"].expiry is None
        assert rows["EUUZ6 C1210"].expiry == date(2026, 12, 4) and rows["EUUZ6 C1210"].option_right == "C"
        # a poorer sighting (no expiry) does not erase the listed one
        poorer = dict(fop, expiry=None)
        await upsert_instruments(db, [poorer])
        assert (await db.get(Instrument, 5)).expiry == date(2026, 12, 4)
    assert instrument_from_ib_position({"con_id": 0, "local_symbol": "X"}) is None


# ── booked-trade check ───────────────────────────────────────────────────────

async def test_booked_check_matches_the_filled_contract_and_never_closes(sqlite_sessionmaker: Any) -> None:
    """Trade 60 on prod: booked strike 1.07720, filled as P1075. The rebuilt
    key 'EUUZ6 P1077' matched nothing and the trade was closed after an hour."""
    async with sqlite_sessionmaker() as db:
        s, _ = await _leg(db, "EUUZ6 P1075", 100, ctype="put", strike=1.0772, expiry=date(2026, 12, 20))
        db.add(BookedPosition(structure_id=s.id, opened_at=datetime(2026, 7, 23, tzinfo=UTC),
                              entry_premium_usd=1.0, entry_total_cost_usd=0.0, state="open"))
        await db.commit()
        held = _Executor([{"local_symbol": "EUUZ6 P1075", "position": 100}])
        assert (await reconcile_trade_positions(db, held)) == {"reconciled": 1, "closed": 0}
        pos = (await db.execute(select(BookedPosition))).scalar_one()
        assert pos.ib_qty_total == 100 and pos.ib_qty_diff == 0 and pos.state == "open"
        # the broker holds none of it: a break for the reconciler, never a close here
        await reconcile_trade_positions(db, _Executor([]))
        pos = (await db.execute(select(BookedPosition))).scalar_one()
        assert pos.ib_qty_diff == 100 and pos.state == "open"


async def test_a_flat_leg_does_not_own_the_contract(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        old, _ = await _leg(db, "EUUG7 C1110", 10)
        new, o_new = await _leg(db, "EUUG7 C1110", 10)
        assert (await _build_leg_to_trade_map(db))["EUUG7 C1110"][1] == new.id
        lp = (await db.execute(select(LegPosition).where(LegPosition.order_id == o_new.id))).scalar_one()
        lp.open_qty = Decimal(0)     # the newer trade was closed
        await db.flush()
        assert (await _build_leg_to_trade_map(db))["EUUG7 C1110"][1] == old.id


# ── repair ───────────────────────────────────────────────────────────────────

async def test_repair_gives_the_leg_its_quantity_and_yields_to_real_fills(sqlite_sessionmaker: Any) -> None:
    """Trade 87 on prod: order 108 'filled', qty_filled 10, zero fills, no leg."""
    async with sqlite_sessionmaker() as db:
        _, o = await _leg(db, "EUUG7 P1120", 10, ctype="put", fill=False)
        assert await _open_qty(db, o.id) == 0
        ev = await record_repair(db, order=o, price=0.02, now=NOW)
        await db.flush()
        assert ev is not None and ev.kind == "repair" and float(ev.qty) == 10
        assert await _open_qty(db, o.id) == 10
        assert await record_repair(db, order=o, price=0.02, now=NOW) is None, "idempotent"
        # the real executions arrive later (reconnect replay): no double count
        db.add(StructureFill(order_id=o.id, ib_execution_id="late", timestamp=NOW, qty_filled=10,
                             fill_price=0.021, commission_usd=0.0, side="BUY"))
        await db.flush()
        await rebuild_leg(db, order_id=o.id)
        await db.flush()
        assert await _open_qty(db, o.id) == 10

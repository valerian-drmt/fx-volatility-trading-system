"""Restart reconciliation (#339, audit 07#4): after a restart the execution
engine rebuilds its view of the book from IB and the DB. On a real SQLite
schema (FKs on):

* ``sync_positions_from_ib`` upserts the IB mirror, never deletes on an
  untrusted snapshot (feed not reporting, or IB flat while the DB holds
  rows), and deletes a row IB no longer holds when the snapshot is trusted;
* ``reconcile_positions`` opens one ``reconciliation_break`` per contract
  where the forward book and the IB mirror disagree, updates it while the
  gap persists, resolves it when they agree, and never runs on a dead feed.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select

from engines.execution.position_sync import sync_positions_from_ib
from engines.execution.reconciler import reconcile_positions
from persistence.models import (
    LegPosition,
    OpenPosition,
    ReconciliationBreak,
    StructureOrder,
    TradeStructure,
)

pytestmark = pytest.mark.asyncio

CALL = "EUUN6 C1170"
FUT = "6EZ6"


class _Executor:
    def __init__(self, positions: list[dict] | None = None, *, connected: bool = True, reporting: bool = True) -> None:
        self._positions = positions or []
        self._connected = connected
        self._reporting = reporting

    def is_connected(self) -> bool:
        return self._connected

    def account_is_reporting(self) -> bool:
        return self._reporting

    async def list_positions(self) -> list[dict]:
        return list(self._positions)


def _ib(local_symbol: str, position: float, *, avg_cost: float = 1500.0, symbol: str = "EUR",
        sec_type: str = "FOP", expiry: str | None = "20260904", con_id: int = 123) -> dict:
    return {"account": "DU1", "symbol": symbol, "sec_type": sec_type, "expiry": expiry, "strike": None,
            "right": None, "exchange": "CME", "currency": "USD", "local_symbol": local_symbol,
            "con_id": con_id, "position": position, "avg_cost": avg_cost}


async def _seed_mirror(maker: Any, local_symbol: str, qty: int, side: str = "BUY") -> int:
    async with maker() as db:
        row = OpenPosition(structure=local_symbol, side=side, quantity=Decimal(qty),
                           entry_timestamp=datetime.now(UTC))
        db.add(row)
        await db.commit()
        return row.id


async def _seed_book(maker: Any, local_symbol: str, open_qty: int) -> int:
    """One structure with one entry leg and its forward leg_position."""
    async with maker() as db:
        s = TradeStructure(structure_type="call", reference_tenor="3M", base_qty=open_qty,
                           state="fully_filled", execution_mode="live")
        db.add(s)
        await db.flush()
        o = StructureOrder(structure_id=s.id, leg_idx=0, order_role="entry", contract_type="call",
                           side="BUY", qty=open_qty, order_type="LMT", limit_price=0.0123,
                           preview_price=0.012, state="filled", ib_local_symbol=local_symbol)
        db.add(o)
        await db.flush()
        db.add(LegPosition(order_id=o.id, open_qty=Decimal(open_qty), rebuilt_at=datetime.now(UTC)))
        await db.commit()
        return o.id


async def _mirror(maker: Any) -> dict[str, tuple[str, int]]:
    async with maker() as db:
        rows = (await db.execute(select(OpenPosition))).scalars().all()
        return {r.structure: (r.side, int(r.quantity)) for r in rows}


async def _breaks(maker: Any) -> list[ReconciliationBreak]:
    async with maker() as db:
        return list((await db.execute(select(ReconciliationBreak).order_by(ReconciliationBreak.id))).scalars().all())


# ---- sync_positions_from_ib ---------------------------------------------------------------------

async def test_sync_refuses_to_touch_the_mirror_when_ib_is_not_connected(sqlite_sessionmaker: Any) -> None:
    await _seed_mirror(sqlite_sessionmaker, CALL, 5)
    async with sqlite_sessionmaker() as db:
        out = await sync_positions_from_ib(db, _Executor(connected=False))  # type: ignore[arg-type]
    assert out["error"] == "ib_not_connected"
    assert await _mirror(sqlite_sessionmaker) == {CALL: ("BUY", 5)}


async def test_sync_opens_updates_and_keeps_rows_from_a_trusted_snapshot(sqlite_sessionmaker: Any) -> None:
    await _seed_mirror(sqlite_sessionmaker, CALL, 5)
    ex = _Executor([_ib(CALL, 7.0), _ib(FUT, -2.0, sec_type="FUT", expiry="20261214", avg_cost=137_000.0)])
    async with sqlite_sessionmaker() as db:
        out = await sync_positions_from_ib(db, ex)  # type: ignore[arg-type]
    assert (out["opened"], out["closed"], out["deletes_skipped"]) == (1, 0, False)
    assert await _mirror(sqlite_sessionmaker) == {CALL: ("BUY", 7), FUT: ("SELL", 2)}

    # Unchanged snapshot: nothing opens, nothing closes, the row count holds.
    async with sqlite_sessionmaker() as db:
        out = await sync_positions_from_ib(db, ex)  # type: ignore[arg-type]
    assert (out["opened"], out["closed"], out["unchanged"]) == (0, 0, 2)


async def test_sync_deletes_a_row_ib_no_longer_holds_only_on_a_trusted_snapshot(sqlite_sessionmaker: Any) -> None:
    await _seed_mirror(sqlite_sessionmaker, CALL, 5)
    await _seed_mirror(sqlite_sessionmaker, FUT, 1)

    # Feed not reporting: the missing contract is NOT a close.
    async with sqlite_sessionmaker() as db:
        out = await sync_positions_from_ib(db, _Executor([_ib(CALL, 5.0)], reporting=False))  # type: ignore[arg-type]
    assert out["closed"] == 0 and out["deletes_skipped"] is True
    assert set(await _mirror(sqlite_sessionmaker)) == {CALL, FUT}

    # IB says flat while the DB holds rows: a dead feed looks exactly like this, so no deletes.
    async with sqlite_sessionmaker() as db:
        out = await sync_positions_from_ib(db, _Executor([], reporting=True))  # type: ignore[arg-type]
    assert out["closed"] == 0 and out["deletes_skipped"] is True
    assert set(await _mirror(sqlite_sessionmaker)) == {CALL, FUT}

    # Reporting feed with a partial book: the row IB dropped is closed.
    async with sqlite_sessionmaker() as db:
        out = await sync_positions_from_ib(db, _Executor([_ib(CALL, 5.0)], reporting=True))  # type: ignore[arg-type]
    assert out["closed"] == 1 and out["deletes_skipped"] is False
    assert set(await _mirror(sqlite_sessionmaker)) == {CALL}


# ---- reconcile_positions --------------------------------------------------------------------------

async def test_reconcile_never_fabricates_a_break_on_a_dead_feed(sqlite_sessionmaker: Any) -> None:
    await _seed_book(sqlite_sessionmaker, CALL, 5)  # book 5, mirror empty
    out = await reconcile_positions(sqlite_sessionmaker, _Executor(reporting=False))
    assert out == {"open_breaks": 0, "skipped": "account_not_reporting"}
    assert await _breaks(sqlite_sessionmaker) == []


async def test_reconcile_opens_updates_then_resolves_a_quantity_break(sqlite_sessionmaker: Any) -> None:
    await _seed_book(sqlite_sessionmaker, CALL, 5)
    pid = await _seed_mirror(sqlite_sessionmaker, CALL, 3)
    t0 = datetime(2026, 11, 7, 9, 0, tzinfo=UTC)

    out = await reconcile_positions(sqlite_sessionmaker, _Executor(), now=t0)
    assert out == {"open_breaks": 1}
    (br,) = await _breaks(sqlite_sessionmaker)
    assert (br.local_symbol, float(br.book_qty), float(br.broker_qty), float(br.diff)) == (CALL, 5.0, 3.0, 2.0)
    assert br.break_type == "quantity" and br.resolved_at is None

    # Gap persists: same row, last_seen advances, no duplicate.
    t1 = t0.replace(minute=1)
    await reconcile_positions(sqlite_sessionmaker, _Executor(), now=t1)
    (br,) = await _breaks(sqlite_sessionmaker)
    assert br.last_seen_at.replace(tzinfo=UTC) == t1 and br.detected_at.replace(tzinfo=UTC) == t0

    # IB catches up: the break resolves, nothing new opens.
    async with sqlite_sessionmaker() as db:
        row = await db.get(OpenPosition, pid)
        row.quantity = Decimal(5)
        await db.commit()
    t2 = t0.replace(minute=2)
    out = await reconcile_positions(sqlite_sessionmaker, _Executor(), now=t2)
    assert out == {"open_breaks": 0}
    (br,) = await _breaks(sqlite_sessionmaker)
    assert br.resolved_at is not None and br.resolved_at.replace(tzinfo=UTC) == t2


async def test_reconcile_classifies_missing_at_ib_and_unbooked_at_ib(sqlite_sessionmaker: Any) -> None:
    await _seed_book(sqlite_sessionmaker, CALL, 5)          # book only
    await _seed_mirror(sqlite_sessionmaker, FUT, 1, "SELL")  # IB only
    out = await reconcile_positions(sqlite_sessionmaker, _Executor())
    assert out == {"open_breaks": 2}
    by = {b.local_symbol: b for b in await _breaks(sqlite_sessionmaker)}
    assert by[CALL].break_type == "missing_at_ib" and float(by[CALL].diff) == 5.0
    assert by[FUT].break_type == "unbooked_at_ib" and float(by[FUT].broker_qty) == -1.0


async def test_a_new_break_is_logged_counted_and_gauged_once(sqlite_sessionmaker: Any, caplog: pytest.LogCaptureFixture) -> None:
    """#323: a new break raises one warning + one counter increment; a persisting
    break does not repeat them; the gauge follows the open count; resolve logs info."""
    import logging

    from shared.observability import reconciliation_breaks_open, reconciliation_breaks_opened_total

    def opened() -> float:
        return reconciliation_breaks_opened_total.labels(break_type="quantity")._value.get()

    await _seed_book(sqlite_sessionmaker, CALL, 5)
    pid = await _seed_mirror(sqlite_sessionmaker, CALL, 3)
    before = opened()
    t0 = datetime(2026, 11, 7, 9, 0, tzinfo=UTC)
    with caplog.at_level(logging.INFO, logger="engines.execution.reconciler"):
        await reconcile_positions(sqlite_sessionmaker, _Executor(), now=t0)
        await reconcile_positions(sqlite_sessionmaker, _Executor(), now=t0.replace(minute=1))
        assert opened() - before == 1
        assert reconciliation_breaks_open._value.get() == 1
        warned = [r for r in caplog.records if r.getMessage() == "reconciliation_break_opened"]
        assert len(warned) == 1 and warned[0].levelno == logging.WARNING and warned[0].contract == CALL

        async with sqlite_sessionmaker() as db:
            row = await db.get(OpenPosition, pid)
            row.quantity = Decimal(5)
            await db.commit()
        await reconcile_positions(sqlite_sessionmaker, _Executor(), now=t0.replace(minute=2))
    assert reconciliation_breaks_open._value.get() == 0
    resolved = [r for r in caplog.records if r.getMessage() == "reconciliation_break_resolved"]
    assert len(resolved) == 1 and resolved[0].open_for_s == 120.0


def test_the_broker_mirror_never_closes_a_booked_trade() -> None:
    """#464: the check that closed a booked trade "flat at IB" matched on a
    symbol rebuilt from the preview and closed trades that were still held.
    The book closes a trade (persistence.book_close); the sync only informs."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[4] / "src/engines/execution/position_sync.py").read_text(encoding="utf-8")
    assert "reconciled_flat_at_ib\"" not in src.split("async def reconcile_trade_positions(")[1].split('"""')[2]
    assert 'tp.state = "closed"' not in src

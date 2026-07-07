"""OMS invariants I1..I7 — executable spec of docs/order-pipeline/OMS_ARCHITECTURE_CIBLE.md §3.2.

Phase-0 red baseline (spec §11.3 step 1). Two invariants already hold on the
current schema and pass today:

  * I1 — projection consistency  (order.qty_filled == Σ its fills)
  * I6 — execution idempotency   (unique ib_execution_id, replay-safe)

The five others are the root defects D1..D4 made executable. They are marked
``xfail(strict=True)`` : they FAIL today (missing module / model / endpoint),
and the phase that fixes each one MUST remove its marker in the same PR
(a strict xfail that unexpectedly passes fails CI — the marker cannot rot).

  * I2 — liveness / terminalisation        → GREEN since P0 (terminal FSM + reaper, spec §6)
  * I3 — forward attribution               → GREEN since P1 (position projector, spec §7.1)
  * I4 — reconciliation breaks materialised → GREEN since P1 (reconcile(), spec §7.2)
  * I5 — reservation ledger, available ≥ 0 → phase P2  (reserved_qty, spec §8)
  * I7 — mirror never display authority    → GREEN since P1 (panel reads the book, spec §7.1)

Contracts pinned by these tests (later phases implement to match):
  * engines.execution.reaper.reap_stale_orders(sessionmaker_factory=, executor=, tau_stale_s=) -> int
    with TERMINAL_STATES == {"filled", "rejected", "cancelled", "expired"}
  * engines.execution.position_projector.project_leg(db, order_id=) -> obj(open_qty, avg_price)
  * engines.execution.reconciler.reconcile_positions(sessionmaker_factory=) -> int
    + persistence.models.ReconciliationBreak (book_qty / broker_qty / diff / break_type / resolved_at)
  * persistence.models.LegPosition (1 row per entry trade_order : open_qty, reserved_qty, avg_price)
  * core.execution.reservation.try_reserve / available / OverReserveError
  * api.routers.positions.list_book(db) — panel read of the book projection
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

pytest.importorskip("pytest_asyncio")

pytestmark = pytest.mark.asyncio


# ──────────────────────────────────────────────────────────────────────
# Shared fixtures (same in-memory aiosqlite pattern as the other unit suites)
# ──────────────────────────────────────────────────────────────────────

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


def _fake_trade(*, status: str = "Submitted") -> SimpleNamespace:
    return SimpleNamespace(orderStatus=SimpleNamespace(status=status))


def _fake_fill(*, exec_id: str, qty: int, price: float, side: str = "BOT",
               commission: float = 2.0) -> SimpleNamespace:
    return SimpleNamespace(
        execution=SimpleNamespace(
            execId=exec_id, shares=qty, price=price, side=side,
            time=datetime.now(UTC), exchange="CME",
        ),
        commissionReport=SimpleNamespace(commission=commission),
    )


async def _seed_leg(maker, *, qty: int = 10, side: str = "BUY",
                    state: str = "submitted", local_symbol: str | None = None,
                    submitted_at: datetime | None = None,
                    ib_order_id: str | None = None,
                    order_role: str = "entry") -> tuple[int, int]:
    """One structure with a single leg ; returns (structure_id, order_id)."""
    from persistence.models import StructureOrder, TradeStructure
    async with maker() as db:
        s = TradeStructure(
            structure_type="strangle", reference_tenor="3M",
            base_qty=qty, state="submitted", execution_mode="live",
        )
        db.add(s)
        await db.flush()
        o = StructureOrder(
            structure_id=s.id, leg_idx=0, order_role=order_role,
            contract_type="call", side=side, qty=qty, order_type="LMT",
            limit_price=1.234, preview_price=1.23, state=state,
            ib_local_symbol=local_symbol,
            submitted_at=submitted_at, ib_order_id=ib_order_id,
        )
        db.add(o)
        await db.flush()
        sid, oid = s.id, o.id
        await db.commit()
        return sid, oid


async def _add_fill(maker, *, order_id: int, exec_id: str, qty: int,
                    price: float, side: str = "BUY") -> None:
    from persistence.models import StructureFill
    async with maker() as db:
        db.add(StructureFill(
            order_id=order_id, ib_execution_id=exec_id,
            timestamp=datetime.now(UTC), qty_filled=qty, fill_price=price,
            commission_usd=1.0, side=side,
        ))
        await db.commit()


class _StubExecutor:
    """Duck-typed EMS/IB view the reaper consumes (spec §6.2)."""

    def __init__(self, *, reporting: bool = True,
                 live_order_ids: tuple[str, ...] = (),
                 held: dict[str, float] | None = None) -> None:
        self._reporting = reporting
        self._live = set(live_order_ids)
        self._held = dict(held or {})

    def account_is_reporting(self) -> bool:
        return self._reporting

    async def is_order_live(self, ib_order_id: str | None) -> bool:
        return ib_order_id is not None and str(ib_order_id) in self._live

    async def held_contracts(self) -> dict[str, float]:
        return dict(self._held)

    async def recent_fills(self) -> list:
        return []


# ──────────────────────────────────────────────────────────────────────
# I1 — projection consistency : order.qty_filled == Σ execution.qty
# ──────────────────────────────────────────────────────────────────────

async def test_i1_order_qty_filled_equals_sum_of_its_fills():
    """Drive the real fill handler with two partials ; the aggregate on the
    order must equal the fold of its own trade_fill rows."""
    from sqlalchemy import func, select

    from engines.execution.fills_handler import _on_execution
    from persistence.models import StructureFill, StructureOrder

    maker, engine = await _make_session()
    try:
        _sid, oid = await _seed_leg(maker, qty=10)
        await _on_execution(_fake_trade(), _fake_fill(exec_id="e1", qty=6, price=1.20), oid, maker)
        await _on_execution(_fake_trade(), _fake_fill(exec_id="e2", qty=4, price=1.30), oid, maker)

        async with maker() as db:
            order = await db.get(StructureOrder, oid)
            total = (await db.execute(
                select(func.coalesce(func.sum(StructureFill.qty_filled), 0))
                .where(StructureFill.order_id == oid)
            )).scalar_one()
        assert order.qty_filled == total == 10
        assert order.state == "filled"
    finally:
        await engine.dispose()


# ──────────────────────────────────────────────────────────────────────
# I2 — liveness : every order older than τ_max reaches a TERMINAL state
# ──────────────────────────────────────────────────────────────────────

async def test_i2_stale_working_order_is_terminalised_by_the_reaper():
    """An order working for > τ_max that IB neither works nor holds must be
    driven to the absorbing state ``expired`` — never left alive (the 91h bug)."""
    from engines.execution.reaper import TERMINAL_STATES, reap_stale_orders
    from persistence.models import StructureOrder

    maker, engine = await _make_session()
    try:
        stale = datetime.now(UTC) - timedelta(hours=2)
        _sid, oid = await _seed_leg(
            maker, state="submitted", submitted_at=stale, ib_order_id="901",
        )
        n = await reap_stale_orders(
            sessionmaker_factory=maker,
            executor=_StubExecutor(reporting=True),   # not live at IB, holds nothing
            tau_stale_s=300,
        )
        async with maker() as db:
            order = await db.get(StructureOrder, oid)
        assert {"filled", "rejected", "cancelled", "expired"} == TERMINAL_STATES
        assert n == 1
        assert order.state == "expired"
        assert order.state in TERMINAL_STATES
    finally:
        await engine.dispose()


# ──────────────────────────────────────────────────────────────────────
# I3 — forward attribution : leg position == fold of ITS fills (via FK)
# ──────────────────────────────────────────────────────────────────────

async def test_i3_leg_position_is_a_pure_fold_of_its_own_fills():
    """Two structures trade the SAME contract (one long 10, one short 4).
    Forward attribution must give +10 and −4 per leg — never the netted +6
    the IB mirror reports."""
    from engines.execution.position_projector import project_leg

    maker, engine = await _make_session()
    try:
        _sa, oa = await _seed_leg(maker, qty=10, side="BUY", state="filled",
                                  local_symbol="EUUV6 C1130")
        _sb, ob = await _seed_leg(maker, qty=4, side="SELL", state="filled",
                                  local_symbol="EUUV6 C1130")
        await _add_fill(maker, order_id=oa, exec_id="a1", qty=6, price=1.20, side="BUY")
        await _add_fill(maker, order_id=oa, exec_id="a2", qty=4, price=1.20, side="BUY")
        await _add_fill(maker, order_id=ob, exec_id="b1", qty=4, price=1.25, side="SELL")

        async with maker() as db:
            pos_a = await project_leg(db, order_id=oa)
            pos_b = await project_leg(db, order_id=ob)
        assert pos_a.open_qty == +10          # long leg : signed fold of its 2 fills
        assert pos_a.avg_price == pytest.approx(1.20)
        assert pos_b.open_qty == -4           # short leg : NOT netted with A
    finally:
        await engine.dispose()


# ──────────────────────────────────────────────────────────────────────
# I4 — reconciliation : any book↔broker gap is materialised as a break row
# ──────────────────────────────────────────────────────────────────────

async def test_i4_book_vs_broker_gap_is_materialised_then_resolved():
    """Book holds +10 of a contract, the mirror says +6 : reconcile() must
    write a ``quantity`` break with diff 4 (never a silent divergence), and
    resolve it once the two sides agree."""
    from sqlalchemy import select

    from engines.execution.reconciler import reconcile_positions
    from persistence.models import LegPosition, OpenPosition, ReconciliationBreak

    maker, engine = await _make_session()
    try:
        _sid, oid = await _seed_leg(maker, qty=10, side="BUY", state="filled",
                                    local_symbol="EUUV6 C1130")
        async with maker() as db:
            db.add(LegPosition(order_id=oid, open_qty=10, reserved_qty=0, avg_price=1.20))
            db.add(OpenPosition(structure="EUUV6 C1130", side="BUY", quantity=6,
                                entry_timestamp=datetime.now(UTC)))
            await db.commit()

        await reconcile_positions(sessionmaker_factory=maker)
        async with maker() as db:
            brk = (await db.execute(select(ReconciliationBreak))).scalars().one()
        assert brk.diff == 4
        assert brk.break_type == "quantity"
        assert brk.resolved_at is None

        async with maker() as db:   # broker catches up → same run resolves it
            mirror = (await db.execute(select(OpenPosition))).scalars().one()
            mirror.quantity = 10
            await db.commit()
        await reconcile_positions(sessionmaker_factory=maker)
        async with maker() as db:
            brk = (await db.execute(select(ReconciliationBreak))).scalars().one()
        assert brk.resolved_at is not None
    finally:
        await engine.dispose()


# ──────────────────────────────────────────────────────────────────────
# I5 — reservation ledger : available = open − reserved ≥ 0, race-free
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.xfail(
    strict=True,
    reason="D4 — no reserved_qty ledger yet ; goes green in P2 (spec §8)",
)
async def test_i5_reservation_ledger_prevents_over_close():
    """The double-click over-close : reserving 7 of 10 leaves available 3 ;
    a second close for 5 must be refused by the invariant, not by a
    stateless re-sum."""
    from core.execution.reservation import OverReserveError, available, try_reserve
    from persistence.models import LegPosition

    assert "reserved_qty" in LegPosition.__table__.c   # materialised, survives restart

    reserved = try_reserve(open_qty=10, reserved_qty=0, requested=7)
    assert reserved == 7
    assert available(open_qty=10, reserved_qty=reserved) == 3

    with pytest.raises(OverReserveError):
        try_reserve(open_qty=10, reserved_qty=reserved, requested=5)

    # Releasing the filled part can never drive available negative.
    assert available(open_qty=3, reserved_qty=try_reserve(
        open_qty=3, reserved_qty=0, requested=3,
    )) == 0


# ──────────────────────────────────────────────────────────────────────
# I6 — execution idempotency : one broker exec id == one execution row
# ──────────────────────────────────────────────────────────────────────

async def test_i6_execution_id_is_unique_and_replay_safe():
    """The append-only truth dedupes on ib_execution_id : declared UNIQUE on
    the table, and a replayed IB event must not double-count the fill."""
    from sqlalchemy import select

    from engines.execution.fills_handler import _on_execution
    from persistence.models import StructureFill, StructureOrder

    assert StructureFill.__table__.c.ib_execution_id.unique is True

    maker, engine = await _make_session()
    try:
        _sid, oid = await _seed_leg(maker, qty=10)
        fill = _fake_fill(exec_id="dup-1", qty=6, price=1.20)
        await _on_execution(_fake_trade(), fill, oid, maker)
        await _on_execution(_fake_trade(), fill, oid, maker)   # replayed event

        async with maker() as db:
            rows = (await db.execute(select(StructureFill))).scalars().all()
            order = await db.get(StructureOrder, oid)
        assert len(rows) == 1
        assert order.qty_filled == 6
    finally:
        await engine.dispose()


# ──────────────────────────────────────────────────────────────────────
# I7 — the mirror is never the display authority
# ──────────────────────────────────────────────────────────────────────

async def test_i7_panel_reads_the_book_projection_not_the_mirror():
    """The book projection says we hold +10 ; the mirror is EMPTY (sync lag,
    feed flap...). The panel read must still show the +10 — holdings come
    from the projection, the mirror is only a reconciliation checksum."""
    from api.routers.positions import list_book
    from persistence.models import LegPosition

    maker, engine = await _make_session()
    try:
        _sid, oid = await _seed_leg(maker, qty=10, side="BUY", state="filled",
                                    local_symbol="EUUV6 C1130")
        async with maker() as db:
            db.add(LegPosition(order_id=oid, open_qty=10, reserved_qty=0, avg_price=1.20))
            await db.commit()
            out = await list_book(db)   # open_position deliberately left empty

        assert len(out) == 1
        assert out[0]["contract"] == "EUUV6 C1130"
        assert out[0]["open_qty"] == 10
    finally:
        await engine.dispose()

"""IB session resilience (#321, audit 04#5 / 04#8 / 04#9).

What a reconnect must restore, with fakes only: handlers back on every open IB
order that maps to a DB row, the day's executions replayed through the
idempotent fill path, the ready gate shut until the pass completes, and the
1100 / 1101 / 1102 codes counted and acted on.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("pytest_asyncio")

pytestmark = pytest.mark.asyncio


class _Executor:
    """OrderExecutor stand-in: open trades + replayable fills are injected."""

    def __init__(self, *, connected: bool = True, open_trades: list[Any] | None = None,
                 fills: list[Any] | None = None) -> None:
        self._connected = connected
        self._open = list(open_trades or [])
        self._fills = list(fills or [])
        self.on_error = None
        self.on_disconnected = None
        self.replay_calls = 0

    def is_connected(self) -> bool:
        return self._connected

    def open_trades(self) -> list[Any]:
        return list(self._open) if self._connected else []

    async def replay_executions(self, timeout: float = 15.0) -> list[Any]:
        self.replay_calls += 1
        return list(self._fills)


async def _seed_structure(maker: Any, *, qty: int = 5, state: str = "submitted") -> tuple[int, int]:
    from persistence.models import StructureOrder, TradeStructure
    async with maker() as db:
        s = TradeStructure(structure_type="straddle", reference_tenor="3M", base_qty=qty,
                           state="submitted", execution_mode="live")
        db.add(s)
        await db.flush()
        o = StructureOrder(structure_id=s.id, leg_idx=0, order_role="entry", contract_type="call",
                           side="BUY", qty=qty, order_type="LMT", limit_price=0.0123,
                           preview_price=0.0123, state=state)
        db.add(o)
        await db.flush()
        await db.commit()
        return int(s.id), int(o.id)


async def _seed_spot(maker: Any, *, qty: int = 25_000) -> int:
    from persistence.models import SpotOrder
    async with maker() as db:
        row = SpotOrder(symbol="EUR", currency="USD", exchange="IDEALPRO", side="BUY", qty=qty,
                        order_type="LMT", limit_price=1.1225, state="submitted")
        db.add(row)
        await db.commit()
        return int(row.id)


def _replay_fill(ib, *, order_ref: str, exec_id: str, qty: int, price: float, contract=None):
    f = ib.fake_fill(exec_id=exec_id, qty=qty, price=price, contract=contract)
    f.execution.orderRef = order_ref
    return f


# ---------------------------------------------------------------- rehydrate ---

async def test_rehydrate_reattaches_handlers_on_open_option_orders(sqlite_sessionmaker, ib_factories) -> None:
    from engines.execution.ib_session import rehydrate_session
    from persistence.models import StructureOrder

    sid, oid = await _seed_structure(sqlite_sessionmaker, state="pending")
    trade = ib_factories.fake_trade(order_id=41, perm_id=9001, order_ref=f"fxvol:{sid}:{oid}")
    ex = _Executor(open_trades=[trade])

    out = await rehydrate_session(ex, sqlite_sessionmaker)

    assert out["option_orders_rewired"] == [oid]
    assert trade.statusEvent.handlers and trade.fillEvent.handlers, "handlers must be back on the new Trade"
    async with sqlite_sessionmaker() as db:
        row = await db.get(StructureOrder, oid)
    assert row.ib_order_id == "41" and row.ib_perm_id == "9001"
    assert row.state == "submitted", "a pending ghost IB holds is submitted"


async def test_rehydrate_ignores_terminal_rows_and_foreign_refs(sqlite_sessionmaker, ib_factories) -> None:
    from engines.execution.ib_session import rehydrate_session

    sid, oid = await _seed_structure(sqlite_sessionmaker, state="filled")
    done = ib_factories.fake_trade(order_ref=f"fxvol:{sid}:{oid}")
    foreign = ib_factories.fake_trade(order_ref="manual:something")
    ex = _Executor(open_trades=[done, foreign])

    out = await rehydrate_session(ex, sqlite_sessionmaker)

    assert out["option_orders_rewired"] == []
    assert not done.statusEvent.handlers and not foreign.statusEvent.handlers


async def test_rehydrate_replays_executions_into_the_fill_table_idempotently(sqlite_sessionmaker, ib_factories) -> None:
    """A fill that landed while the engine was down is booked once, and a second
    replay (next reconnect) adds nothing."""
    from sqlalchemy import select

    from engines.execution.ib_session import rehydrate_session
    from persistence.models import StructureFill, StructureOrder

    sid, oid = await _seed_structure(sqlite_sessionmaker, qty=5)
    fills = [
        _replay_fill(ib_factories, order_ref=f"fxvol:{sid}:{oid}", exec_id="e.1", qty=3, price=0.0120),
        _replay_fill(ib_factories, order_ref=f"fxvol:{sid}:{oid}", exec_id="e.2", qty=2, price=0.0124),
        _replay_fill(ib_factories, order_ref="fxvol:999:999", exec_id="e.3", qty=1, price=0.5),
    ]
    ex = _Executor(fills=fills)

    first = await rehydrate_session(ex, sqlite_sessionmaker)
    second = await rehydrate_session(ex, sqlite_sessionmaker)

    assert first["fills_replayed"] == 2 and first["fills_unmatched"] == 1
    assert second["fills_replayed"] == 2, "replay runs again but persists nothing new"
    async with sqlite_sessionmaker() as db:
        rows = (await db.execute(select(StructureFill).where(StructureFill.order_id == oid))).scalars().all()
        order = await db.get(StructureOrder, oid)
    assert sorted(r.ib_execution_id for r in rows) == ["e.1", "e.2"]
    assert order.state == "filled" and int(order.qty_filled) == 5


async def test_rehydrate_folds_spot_fills_and_never_shrinks_a_booked_quantity(sqlite_sessionmaker, ib_factories) -> None:
    from engines.execution.ib_session import rehydrate_session
    from persistence.models import SpotOrder

    spot_id = await _seed_spot(sqlite_sessionmaker, qty=25_000)
    cash = ib_factories.fake_contract(local_symbol="EUR.USD", sec_type="CASH", exchange="IDEALPRO")
    full = [_replay_fill(ib_factories, order_ref=f"fxvol:spot:{spot_id}", exec_id="s.1", qty=25_000,
                         price=1.12245, contract=cash)]
    partial = [_replay_fill(ib_factories, order_ref=f"fxvol:spot:{spot_id}", exec_id="s.1", qty=10_000,
                            price=1.12245, contract=cash)]

    await rehydrate_session(_Executor(fills=full), sqlite_sessionmaker)
    async with sqlite_sessionmaker() as db:
        row = await db.get(SpotOrder, spot_id)
        assert row.state == "filled" and row.qty_filled == 25_000 and row.ib_local_symbol == "EUR.USD"

    await rehydrate_session(_Executor(fills=partial), sqlite_sessionmaker)
    async with sqlite_sessionmaker() as db:
        row = await db.get(SpotOrder, spot_id)
        assert row.qty_filled == 25_000, "a narrower replay window must not un-fill the row"


async def test_rehydrate_on_a_disconnected_executor_does_nothing(sqlite_sessionmaker) -> None:
    from engines.execution.ib_session import rehydrate_session

    ex = _Executor(connected=False)
    out = await rehydrate_session(ex, sqlite_sessionmaker)
    assert out["error"] == "ib_not_connected" and ex.replay_calls == 0


# ------------------------------------------------------------------- gate ---

async def test_gate_is_shut_until_the_first_pass_completes(sqlite_sessionmaker) -> None:
    from engines.execution.ib_session import IbSessionSupervisor

    steps: list[str] = []

    async def sync() -> dict[str, int]:
        steps.append("sync")
        return {"synced": 0}

    ex = _Executor()
    sup = IbSessionSupervisor(ex, sqlite_sessionmaker, extra_steps={"sync_positions": sync})
    sup.install()
    assert ex.on_error == sup.on_error and ex.on_disconnected == sup.on_disconnected
    assert not sup.state.ready.is_set() and sup.state.ready_reason == "not_started"

    summary = await sup.on_connected()

    assert steps == ["sync"]
    assert sup.state.ready.is_set() and sup.state.ready_reason == "ready"
    assert summary["sync_positions"] == {"synced": 0} and "rehydrate" in summary
    assert sup.state.events["connected"] == 1


async def test_a_failing_step_is_reported_and_does_not_keep_the_gate_shut(sqlite_sessionmaker) -> None:
    from engines.execution.ib_session import IbSessionSupervisor

    async def boom() -> None:
        raise RuntimeError("db down")

    sup = IbSessionSupervisor(_Executor(), sqlite_sessionmaker, extra_steps={"reconcile": boom})
    summary = await sup.on_connected()
    assert summary["reconcile_error"] == "RuntimeError"
    assert sup.state.ready.is_set(), "one failed step is logged, not a permanent lock-out"


async def test_pass_on_a_disconnected_executor_leaves_the_gate_shut(sqlite_sessionmaker) -> None:
    from engines.execution.ib_session import IbSessionSupervisor

    sup = IbSessionSupervisor(_Executor(connected=False), sqlite_sessionmaker)
    out = await sup.run_pass(trigger="startup")
    assert out["error"] == "ib_not_connected"
    assert not sup.state.ready.is_set() and sup.state.ready_reason == "ib_not_connected"


async def test_disconnect_closes_the_gate_and_the_reconnect_pass_reopens_it(sqlite_sessionmaker) -> None:
    from engines.execution.ib_session import IbSessionSupervisor

    ex = _Executor()
    sup = IbSessionSupervisor(ex, sqlite_sessionmaker)
    await sup.on_connected()
    assert sup.state.ready.is_set()

    sup.on_disconnected()
    assert not sup.state.ready.is_set() and sup.state.ready_reason == "ib_disconnected"
    assert sup.state.events["disconnected"] == 1

    await sup.on_connected()
    assert sup.state.ready.is_set()


# ------------------------------------------------------------- errorEvent ---

async def test_1100_closes_the_gate_and_1102_reopens_it_without_a_pass(sqlite_sessionmaker) -> None:
    from engines.execution.ib_session import IbSessionSupervisor

    ex = _Executor()
    sup = IbSessionSupervisor(ex, sqlite_sessionmaker)
    await sup.on_connected()
    assert ex.replay_calls == 1

    sup.on_error(-1, 1100, "Connectivity between IB and Trader Workstation has been lost.")
    assert not sup.state.ready.is_set() and sup.state.ready_reason == "ib_upstream_down"
    assert sup.state.upstream_down_since is not None and sup.state.events["upstream_lost"] == 1
    assert not sup.state.upstream_ok

    sup.on_error(-1, 1102, "Connectivity between IB and Trader Workstation has been restored - data maintained.")
    assert sup.state.ready.is_set() and sup.state.upstream_ok
    assert ex.replay_calls == 1, "1102 keeps subscriptions: no rehydration needed"


async def test_1101_reopens_only_after_a_full_pass(sqlite_sessionmaker) -> None:
    from engines.execution.ib_session import IbSessionSupervisor

    ex = _Executor()
    sup = IbSessionSupervisor(ex, sqlite_sessionmaker)
    await sup.on_connected()
    sup.on_error(-1, 1100, "lost")
    sup.on_error(-1, 1101, "restored - data lost")
    # The pass is scheduled, not run inline (ib_insync fires the event synchronously).
    for _ in range(50):
        if sup.state.ready.is_set():
            break
        await asyncio.sleep(0.01)
    assert sup.state.ready.is_set()
    assert ex.replay_calls == 2, "1101 drops subscriptions: handlers and fills are rehydrated"
    assert sup.state.last_pass["trigger"] == "1101"


async def test_farm_notices_are_counted_but_never_touch_the_gate(sqlite_sessionmaker) -> None:
    from engines.execution.ib_session import IbSessionSupervisor, classify_error_code

    sup = IbSessionSupervisor(_Executor(), sqlite_sessionmaker)
    await sup.on_connected()
    for code in (2104, 2106, 2158):
        sup.on_error(-1, code, "farm connection is OK")
    sup.on_error(7, 201, "Order rejected - reason: insufficient margin")
    assert sup.state.ready.is_set()
    assert sup.state.events["farm_status"] == 3 and sup.state.events["order_rejected"] == 1
    assert classify_error_code(507) == "socket_closed" and classify_error_code(110) == "price_increment"


def test_session_metric_is_registered() -> None:
    from shared.observability import ib_session_events_total

    before = ib_session_events_total.labels(engine="t", kind="upstream_lost")._value.get()
    ib_session_events_total.labels(engine="t", kind="upstream_lost").inc()
    assert ib_session_events_total.labels(engine="t", kind="upstream_lost")._value.get() == before + 1


# ------------------------------------------------------- watchdog hooks ---

async def test_watchdog_calls_on_lost_once_and_on_reconnected_after_each_reconnect() -> None:
    from shared.ib_connection import maintain_ib_connection

    class Flaky:
        def __init__(self) -> None:
            self.connected = True
            self.connects = 0

        def is_connected(self) -> bool:
            return self.connected

        async def connect(self, timeout: float = 5.0) -> None:
            self.connects += 1
            self.connected = True

    calls: list[str] = []

    async def reconnected() -> None:
        calls.append("reconnected")

    ex = Flaky()
    stop = asyncio.Event()
    task = asyncio.create_task(maintain_ib_connection(
        ex, stop, interval_s=0.01, on_reconnected=reconnected, on_lost=lambda: calls.append("lost"),
    ))
    await asyncio.sleep(0.03)
    assert calls == [], "a healthy socket triggers nothing"
    ex.connected = False
    for _ in range(100):
        if ex.connects >= 1 and calls.count("reconnected") >= 1:
            break
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)
    assert calls == ["lost", "reconnected"]


# ------------------------------------------------------------ ready dep ---

async def test_require_ready_returns_503_with_the_gate_reason() -> None:
    from fastapi import HTTPException

    from engines.execution.main import require_ready

    gate = SimpleNamespace(state=SimpleNamespace(ready=asyncio.Event(), ready_reason="reconciliation_in_progress"))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(session=gate)))
    with pytest.raises(HTTPException) as ei:
        require_ready(request)  # type: ignore[arg-type]
    assert ei.value.status_code == 503
    assert ei.value.detail == {"code": "reconciliation_in_progress", "reason": "reconciliation_in_progress"}

    gate.state.ready.set()
    require_ready(request)  # type: ignore[arg-type]  # no raise once open


def test_health_tolerates_a_short_upstream_outage_and_fails_a_long_one() -> None:
    from engines.execution.ib_session import SessionState
    from engines.execution.main import _MAX_SYNC_AGE_S

    st = SessionState()
    assert st.upstream_down_s() is None
    st.upstream_down_since = datetime.now(UTC)
    assert st.upstream_down_s() < _MAX_SYNC_AGE_S
    snap = st.snapshot()
    assert snap["ib_upstream_ok"] is False and snap["ready"] is False

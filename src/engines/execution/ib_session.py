"""IB session resilience for the execution engine (#321, audit 04#5 / 04#8 / 04#9).

Three things the engine did not do before this module:

* **errorEvent.** IB signals connectivity on ``ib.errorEvent`` with codes
  1100 (upstream lost; the API socket stays up), 1101 (restored, subscriptions
  lost) and 1102 (restored, subscriptions kept). ``maintain_ib_connection``
  only polls ``isConnected()``, which never sees a 1100. The supervisor counts
  every code in ``ib_session_events_total`` and closes the ready gate on 1100.
* **Rehydration after a (re)connect.** ``OrderExecutor.connect`` builds a new
  ``IB()``, so every ``Trade`` the previous session had handlers on is gone.
  :func:`rehydrate_session` re-attaches the fill / status handlers to every
  open IB order that maps to a DB row (options by ``fxvol:<sid>:<oid>``, spot
  by ``fxvol:spot:<id>``) and replays ``reqExecutions`` into the idempotent
  fill persistence, so a fill that landed while the engine was away is booked.
* **Ready gate.** Mutating routes refuse with 503 ``reconciliation_in_progress``
  until one full pass (rehydrate + position sync + stuck-order reconcile) has
  completed on the current connection. The gate closes again on a socket drop
  or a 1100 and reopens only after the next pass.

The supervisor owns no loop of its own: the lifespan calls
:meth:`IbSessionSupervisor.on_connected` after the startup connect, and the
reconnect watchdog calls it after each successful reconnect.
"""
from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from engines.execution.fills_handler import attach_fill_handlers, persist_fill
from engines.execution.reaper import parse_order_ref
from engines.execution.spot_orders import (
    adopt_open_spot_orders,
    fold_spot_fills,
    parse_spot_order_ref,
)
from persistence.models import SpotOrder, StructureOrder, TradeEvent, TradeStructure
from persistence.order_state import lock_order, mark_submitted, revive_order

logger = logging.getLogger(__name__)

# IB error codes that describe the session rather than one request.
UPSTREAM_LOST = 1100
UPSTREAM_RESTORED_DATA_LOST = 1101
UPSTREAM_RESTORED_DATA_KEPT = 1102
SOCKET_CLOSED = 507
# Farm status notices (2103..2108, 2157/2158): informational, very chatty.
_FARM_CODES = frozenset({2103, 2104, 2105, 2106, 2107, 2108, 2119, 2157, 2158})

_OPEN_ORDER_STATES = ("pending", "submitted", "partially_filled")
_SPOT_OPEN_STATES = ("pending", "submitted", "partially_filled")

ReadyReason = str


def classify_error_code(code: int) -> str:
    """Bucket an IB error code; the label feeds the log line and the metric."""
    if code == UPSTREAM_LOST:
        return "upstream_lost"
    if code == UPSTREAM_RESTORED_DATA_LOST:
        return "restored_data_lost"
    if code == UPSTREAM_RESTORED_DATA_KEPT:
        return "restored_data_kept"
    if code == SOCKET_CLOSED:
        return "socket_closed"
    if code in _FARM_CODES:
        return "farm_status"
    if code == 201:
        return "order_rejected"
    if code == 202:
        return "order_cancelled"
    if code == 399:
        return "order_warning"
    if code == 110:
        return "price_increment"
    return "other"


class SessionState:
    """What the engine knows about its IB session, readable from /health."""

    def __init__(self) -> None:
        self.ready = asyncio.Event()
        self.ready_reason: ReadyReason = "not_started"
        self.upstream_down_since: datetime | None = None
        self.last_pass: dict[str, Any] | None = None
        self.last_pass_at: datetime | None = None
        self.events: Counter[str] = Counter()

    def close(self, reason: ReadyReason) -> None:
        if self.ready.is_set() or self.ready_reason != reason:
            logger.warning("ib_session_gate_closed reason=%s", reason)
        self.ready.clear()
        self.ready_reason = reason

    def open(self) -> None:
        if not self.ready.is_set():
            logger.info("ib_session_gate_open")
        self.ready.set()
        self.ready_reason = "ready"

    @property
    def upstream_ok(self) -> bool:
        return self.upstream_down_since is None

    def upstream_down_s(self, now: datetime | None = None) -> float | None:
        if self.upstream_down_since is None:
            return None
        return max(0.0, ((now or datetime.now(UTC)) - self.upstream_down_since).total_seconds())

    def snapshot(self) -> dict[str, Any]:
        return {
            "ready": self.ready.is_set(),
            "ready_reason": self.ready_reason,
            "ib_upstream_ok": self.upstream_ok,
            "upstream_down_s": self.upstream_down_s(),
            "last_pass_at": self.last_pass_at.isoformat() if self.last_pass_at else None,
            "last_pass": self.last_pass,
            "session_events": dict(self.events),
        }


def _record_event(engine: str, label: str) -> None:
    try:
        from shared.observability import ib_session_events_total

        ib_session_events_total.labels(engine=engine, kind=label).inc()
    except Exception:  # metrics are never load-bearing
        pass


# --------------------------------------------------------------------------
# Rehydration : handlers back on open orders, fills replayed
# --------------------------------------------------------------------------

async def _attach_option_handlers(
    db: AsyncSession, open_trades: list[Any], sm: async_sessionmaker[AsyncSession], now: datetime,
) -> list[int]:
    """Re-wire ``attach_fill_handlers`` on every open non-CASH IB order whose
    ``orderRef`` names a DB order still in flight. Stamps missing IB ids the
    way the reaper's orphan adoption does, so the two paths agree."""
    by_order_id: dict[int, Any] = {}
    for t in open_trades:
        c = getattr(t, "contract", None)
        if (getattr(c, "secType", None) or "").upper() == "CASH":
            continue
        parsed = parse_order_ref(getattr(getattr(t, "order", None), "orderRef", None))
        if parsed is None:
            continue
        by_order_id[parsed[1]] = t
    if not by_order_id:
        return []
    rows = list((await db.execute(
        select(StructureOrder)
        .where(StructureOrder.id.in_(tuple(by_order_id)))
        .where(StructureOrder.state.in_(_OPEN_ORDER_STATES))
    )).scalars().all())
    # An order IB still holds OPEN cannot be rejected or cancelled, whatever
    # the row says: the broker is the authority on its own orders. Rows wrongly
    # terminalised (notice 10349 read as a cancel) come back to ``submitted``,
    # so the desk sees the order working and can cancel it.
    revived = list((await db.execute(
        select(StructureOrder)
        .where(StructureOrder.id.in_(tuple(by_order_id)))
        .where(StructureOrder.state.in_(("rejected", "cancelled", "expired")))
        .where(StructureOrder.qty_filled < StructureOrder.qty)
    )).scalars().all())
    for o in revived:
        await lock_order(db, o)
        if o.state not in ("rejected", "cancelled", "expired"):
            continue   # a handler moved it since the select
        prev = o.state
        revive_order(o, now=now)   # IB lists it open: IB has it
        struct = await db.get(TradeStructure, int(o.structure_id))
        if struct is not None and struct.state == "fully_failed":
            struct.state = "submitted"   # its order is working after all
            struct.state_updated_at = now
        db.add(TradeEvent(
            structure_id=int(o.structure_id), order_id=int(o.id),
            event_type="order_revived_open_at_ib", severity="warning",
            description=f"order {o.id} was '{prev}' in the book but is open at IB: back to submitted"[:300],
            payload={"previous_state": prev},
        ))
        logger.warning("order_revived_open_at_ib order_id=%s previous_state=%s", o.id, prev)
    rows += revived
    wired: list[int] = []
    for o in rows:
        t = by_order_id[int(o.id)]
        if not hasattr(t, "statusEvent") or not hasattr(t, "fillEvent"):
            continue
        order = getattr(t, "order", None)
        if o.ib_order_id is None and getattr(order, "orderId", None) is not None:
            o.ib_order_id = str(order.orderId)
        if o.ib_perm_id is None and getattr(order, "permId", None):
            o.ib_perm_id = str(order.permId)
        await mark_submitted(db, o, now=now)
        attach_fill_handlers(trade=t, order_id=int(o.id), sessionmaker_factory=sm)
        wired.append(int(o.id))
    if wired:
        db.add(TradeEvent(
            structure_id=int(rows[0].structure_id), order_id=None,
            event_type="handlers_reattached", severity="info",
            description=f"handlers re-attached after reconnect on orders {wired}",
            payload={"order_ids": wired},
        ))
    return wired


async def _replay_fills(
    db: AsyncSession, fills: list[Any], sm: async_sessionmaker[AsyncSession],
) -> dict[str, int]:
    """Push every replayed execution through the idempotent persistence. The
    option path dedupes on ``ib_execution_id``; the spot path re-folds the
    cumulative fills of one order, which is idempotent by construction."""
    option_fills: dict[int, list[Any]] = {}
    spot_fills: dict[int, list[Any]] = {}
    unmatched = 0
    for f in fills:
        ex = getattr(f, "execution", None)
        ref = getattr(ex, "orderRef", None)
        parsed = parse_order_ref(ref)
        if parsed is not None:
            option_fills.setdefault(parsed[1], []).append(f)
            continue
        sid = parse_spot_order_ref(ref)
        if sid is not None:
            spot_fills.setdefault(sid, []).append(f)
            continue
        unmatched += 1
    replayed = 0
    if option_fills:
        known = set((await db.execute(
            select(StructureOrder.id).where(StructureOrder.id.in_(tuple(option_fills)))
        )).scalars().all())
        for oid, fs in option_fills.items():
            if oid not in known:
                unmatched += len(fs)
                continue
            for f in fs:
                await persist_fill(f, oid, sm)
                replayed += 1
    spot_folded = 0
    if spot_fills:
        known_spot = set((await db.execute(
            select(SpotOrder.id).where(SpotOrder.id.in_(tuple(spot_fills)))
        )).scalars().all())
        for sid, fs in spot_fills.items():
            if sid not in known_spot:
                unmatched += len(fs)
                continue
            contract = getattr(fs[0], "contract", None)
            await fold_spot_fills(sid, fs, sm, contract=contract)
            spot_folded += 1
    return {"fills_seen": len(fills), "fills_replayed": replayed,
            "spot_orders_folded": spot_folded, "fills_unmatched": unmatched}


async def rehydrate_session(
    executor: Any, sm: async_sessionmaker[AsyncSession],
) -> dict[str, Any]:
    """One rehydration pass on the executor's *current* IB object.

    Returns a summary; never raises (each step is best-effort and logged), so
    the caller can still finish the rest of the reconciliation pass."""
    out: dict[str, Any] = {"option_orders_rewired": [], "spot_orders_rewired": []}
    if not executor.is_connected():
        out["error"] = "ib_not_connected"
        return out
    now = datetime.now(UTC)
    open_trades = executor.open_trades()
    try:
        async with sm() as db:
            out["option_orders_rewired"] = await _attach_option_handlers(db, open_trades, sm, now)
            await db.commit()
    except Exception:
        logger.exception("rehydrate_option_handlers_failed")
        out["option_handlers_error"] = True
    try:
        out["spot_orders_rewired"] = await adopt_open_spot_orders(open_trades, sm)
    except Exception:
        logger.exception("rehydrate_spot_adoption_failed")
        out["spot_handlers_error"] = True
    try:
        fills = await executor.replay_executions()
        async with sm() as db:
            out.update(await _replay_fills(db, fills, sm))
    except Exception:
        logger.exception("rehydrate_fill_replay_failed")
        out["fill_replay_error"] = True
    logger.info("ib_session_rehydrated %s", out)
    return out


# --------------------------------------------------------------------------
# Supervisor : gate + event handlers + the reconciliation pass
# --------------------------------------------------------------------------

PassStep = Callable[[], Awaitable[Any]]


class IbSessionSupervisor:
    """Owns the ready gate and reacts to IB session events.

    ``extra_steps`` run after rehydration inside the same pass (position sync,
    stuck-order reconcile); each gets its own try/except so one failing step
    does not keep the gate shut forever, but the pass summary says what failed.
    """

    def __init__(
        self,
        executor: Any,
        sm: async_sessionmaker[AsyncSession],
        *,
        engine: str = "execution_engine",
        extra_steps: dict[str, PassStep] | None = None,
        pass_timeout_s: float = 60.0,
    ) -> None:
        self.executor = executor
        self.sm = sm
        self.engine = engine
        self.extra_steps = dict(extra_steps or {})
        self.pass_timeout_s = pass_timeout_s
        self.state = SessionState()
        self._pass_lock = asyncio.Lock()
        self._bg: set[asyncio.Task[Any]] = set()
        self.state.close("not_started")

    # -- wiring ------------------------------------------------------------

    def install(self) -> None:
        """Point the executor's session hooks at this supervisor."""
        self.executor.on_error = self.on_error
        self.executor.on_disconnected = self.on_disconnected

    def _spawn(self, coro: Awaitable[Any]) -> None:
        task = asyncio.ensure_future(coro)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    # -- ib_async callbacks (sync; they fire from the event loop) -----------

    def on_error(self, req_id: int, code: int, message: str, contract: Any = None) -> None:
        label = classify_error_code(int(code))
        self.state.events[label] += 1
        _record_event(self.engine, label)
        if label == "farm_status":
            return
        safe = str(message).replace("\r", " ").replace("\n", " ")[:200]
        if code == UPSTREAM_LOST:
            if self.state.upstream_down_since is None:
                self.state.upstream_down_since = datetime.now(UTC)
            self.state.close("ib_upstream_down")
            logger.warning("ib_session_event code=%s kind=%s msg=%s", code, label, safe)
            return
        if code in (UPSTREAM_RESTORED_DATA_KEPT, UPSTREAM_RESTORED_DATA_LOST):
            down_s = self.state.upstream_down_s()
            self.state.upstream_down_since = None
            logger.warning("ib_session_event code=%s kind=%s down_s=%s msg=%s", code, label, down_s, safe)
            if code == UPSTREAM_RESTORED_DATA_LOST:
                # Subscriptions and order state may be gone: full pass.
                self._spawn(self.run_pass(trigger="1101"))
            elif self.state.ready_reason == "ib_upstream_down":
                self.state.open()
            return
        if code == SOCKET_CLOSED:
            self.state.close("ib_socket_closed")
        logger.info("ib_session_event code=%s kind=%s req_id=%s msg=%s", code, label, req_id, safe)

    def on_disconnected(self) -> None:
        self.state.events["disconnected"] += 1
        _record_event(self.engine, "disconnected")
        self.state.close("ib_disconnected")

    # -- the pass ----------------------------------------------------------

    async def on_connected(self) -> dict[str, Any]:
        """Called after every successful connect (startup + watchdog)."""
        self.state.events["connected"] += 1
        _record_event(self.engine, "connected")
        return await self.run_pass(trigger="connected")

    async def run_pass(self, *, trigger: str) -> dict[str, Any]:
        """Rehydrate, then the extra steps; open the gate when the pass ran on a
        live connection. Serialised: a second trigger waits for the first."""
        async with self._pass_lock:
            if not self.executor.is_connected():
                self.state.close("ib_not_connected")
                return {"trigger": trigger, "error": "ib_not_connected"}
            self.state.close("reconciliation_in_progress")
            summary: dict[str, Any] = {"trigger": trigger}
            try:
                summary["rehydrate"] = await asyncio.wait_for(
                    rehydrate_session(self.executor, self.sm), self.pass_timeout_s,
                )
            except Exception as e:  # includes TimeoutError
                logger.exception("ib_session_pass_rehydrate_failed")
                summary["rehydrate_error"] = type(e).__name__
            for name, step in self.extra_steps.items():
                try:
                    summary[name] = await asyncio.wait_for(step(), self.pass_timeout_s)
                except Exception as e:
                    logger.exception("ib_session_pass_step_failed step=%s", name)
                    summary[f"{name}_error"] = type(e).__name__
            self.state.last_pass = summary
            self.state.last_pass_at = datetime.now(UTC)
            if not self.executor.is_connected():
                self.state.close("ib_disconnected")
            elif not self.state.upstream_ok:
                self.state.close("ib_upstream_down")
            else:
                self.state.open()
            logger.info("ib_session_pass_done trigger=%s ready=%s", trigger, self.state.ready.is_set())
            return summary

"""Order reaper — the liveness guarantee of the order FSM (invariant I2).

Spec: docs/order-pipeline/OMS_ARCHITECTURE_CIBLE.md §6. Every order must
reach an absorbing state in bounded time; nothing may rest "working" forever
(the ``submitted 91h`` defect, D1). The ``stuck_order_watcher_loop`` only
ALERTS on those rows — this module terminalises them.

One pass (``reap_stale_orders``), guarded so it can never fabricate state:

  1. ``executor.account_is_reporting()`` false → do nothing. An empty
     snapshot from a dead feed must never be read as "IB is flat".
  2. Candidate = order in a working state older than ``tau_stale_s``.
  3. Order still live at IB (in its open trades) → legitimately resting,
     leave it alone.
  4. IB still reports execution records for the order (missed fill event,
     e.g. after a disconnect) → replay them through the idempotent fill
     handler. The append-only truth gets the REAL executions — never
     synthetic rows — and the order tips to filled/partially_filled by the
     normal aggregate path.
  5. No executions available but IB genuinely holds a matching contract in
     the order's direction → backfill ``filled`` (audit-flagged; the
     position projection will still surface the missing executions as a
     reconciliation break — visible, never silent).
  6. Otherwise → ``expired``. Absorbing, frees stacking guards / closes.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from engines.execution.fills_handler import _on_execution, maybe_complete_structure
from persistence.models import StructureOrder, TradeEvent
from shared.contracts import parse_local_symbol

logger = logging.getLogger(__name__)

#: Absorbing states — invariant I2 : every order lands in one of these in ≤ τ_max.
TERMINAL_STATES: frozenset[str] = frozenset({"filled", "rejected", "cancelled", "expired"})

#: Non-terminal states the reaper considers ("working" set). ``pending`` is
#: excluded on purpose : a pending order was never dispatched — that is the
#: dual-write gap (spec §10.2, P3), not a liveness gap at the broker.
_REAPABLE_STATES = ("submitted", "acknowledged", "partially_filled")

# CME EUR FOP strikes sit on a 0.005 grid ; allow a touch more for float noise.
_STRIKE_TOL = 0.006


def _as_utc(dt: datetime | None) -> datetime | None:
    """sqlite returns naive datetimes ; treat them as UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _order_age_basis(order: StructureOrder) -> datetime | None:
    return _as_utc(order.submitted_at) or _as_utc(order.state_updated_at)


def _held_match(order: StructureOrder, held: dict[str, float]) -> str | None:
    """localSymbol of a held contract that IS this order's contract traded in
    this order's direction — or None. Conservative on purpose : any ambiguity
    (flat net, opposite net sign, spec mismatch) is NOT a match ; we expire
    instead of inventing a fill (spec §6.2 guard #2)."""
    direction = +1 if (order.side or "").upper() == "BUY" else -1

    def _direction_ok(net_qty: float) -> bool:
        return net_qty != 0 and (net_qty > 0) == (direction > 0)

    if order.ib_local_symbol:
        net = held.get(order.ib_local_symbol, 0.0)
        return order.ib_local_symbol if _direction_ok(net) else None

    ct = (order.contract_type or "").lower()
    for local_symbol, net in held.items():
        if not _direction_ok(net):
            continue
        spec = parse_local_symbol(local_symbol)
        if spec is None:
            continue
        if ct == "future":
            if spec.instrument_type == "FUTURE":
                return local_symbol
            continue
        if spec.instrument_type != "OPTION":
            continue
        if (spec.option_type or "").lower() != ct:
            continue
        if order.contract_strike is None or spec.strike is None:
            return local_symbol            # cannot compare → trust side + type
        if abs(float(order.contract_strike) - float(spec.strike)) <= _STRIKE_TOL:
            return local_symbol
    return None


def _fills_by_ib_order(recent_fills: list[Any]) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {}
    for f in recent_fills:
        ib_order_id = getattr(f.execution, "orderId", None)
        if ib_order_id is not None:
            out.setdefault(str(ib_order_id), []).append(f)
    return out


async def reap_stale_orders(
    *,
    sessionmaker_factory: async_sessionmaker[AsyncSession],
    executor: Any,
    tau_stale_s: float = 300.0,
) -> int:
    """One reaper pass. Returns the number of orders terminalised."""
    if not executor.account_is_reporting():
        logger.debug("reaper_skipped account_not_reporting")
        return 0

    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=tau_stale_s)
    async with sessionmaker_factory() as db:
        candidates = (await db.execute(
            select(StructureOrder).where(StructureOrder.state.in_(_REAPABLE_STATES))
        )).scalars().all()
        stale = [
            o for o in candidates
            if (basis := _order_age_basis(o)) is not None and basis < cutoff
        ]

    if not stale:
        return 0

    held = dict(await executor.held_contracts())
    fills_by_order = _fills_by_ib_order(list(await executor.recent_fills()))

    reaped = 0
    for order in stale:
        if order.ib_order_id and await executor.is_order_live(order.ib_order_id):
            continue                        # genuinely resting at IB → not ours to touch

        # Missed fill events : replay the REAL execution records through the
        # idempotent fill handler (dedup on ib_execution_id — I6).
        replayed = False
        for fill in fills_by_order.get(str(order.ib_order_id or ""), []):
            await _on_execution(None, fill, order.id, sessionmaker_factory)
            replayed = True
        if replayed:
            async with sessionmaker_factory() as db:
                refreshed = await db.get(StructureOrder, order.id)
            if refreshed is not None and refreshed.state in TERMINAL_STATES:
                reaped += 1
                continue

        matched_symbol = _held_match(order, held)
        async with sessionmaker_factory() as db:
            db_order = await db.get(StructureOrder, order.id)
            if db_order is None or db_order.state in TERMINAL_STATES:
                continue
            # The held-contract backfill only applies to orders with ZERO real
            # fill rows : on a partial we cannot know the residual's fate from
            # a netted holding, and overwriting real aggregates would break I1.
            # The residual expires ; if that is wrong, reconciliation surfaces
            # it as a break instead of the book silently inventing quantity.
            if matched_symbol is not None and int(db_order.qty_filled or 0) == 0:
                # IB holds the contract in this order's direction but no longer
                # reports the executions : backfill, audit-flagged. Never reached
                # when IB does not hold the contract — no phantom fill.
                db_order.state = "filled"
                db_order.qty_filled = int(db_order.qty)
                if db_order.avg_fill_price is None and db_order.preview_price is not None:
                    db_order.avg_fill_price = float(db_order.preview_price)
                if db_order.ib_local_symbol is None:
                    db_order.ib_local_symbol = matched_symbol[:20]
                db_order.fully_filled_at = now
                db_order.state_updated_at = now
                db.add(TradeEvent(
                    structure_id=db_order.structure_id, order_id=db_order.id,
                    event_type="order_filled_from_ib_position", severity="warning",
                    description=(
                        f"reaper: order {db_order.id} absent from IB open trades but "
                        f"contract {matched_symbol} held in its direction → filled"
                    ),
                    payload={"local_symbol": matched_symbol,
                             "ib_order_id": db_order.ib_order_id},
                ))
            else:
                db_order.state = "expired"
                db_order.state_updated_at = now
                db.add(TradeEvent(
                    structure_id=db_order.structure_id, order_id=db_order.id,
                    event_type="order_expired_by_reaper", severity="warning",
                    description=(
                        f"reaper: order {db_order.id} stale for >{tau_stale_s:.0f}s, "
                        "absent from IB open trades, contract not held → expired"
                    ),
                    payload={"ib_order_id": db_order.ib_order_id,
                             "qty_filled": int(db_order.qty_filled or 0)},
                ))
            structure_id = db_order.structure_id
            terminal_as_filled = db_order.state == "filled"
            await db.commit()
        reaped += 1
        if terminal_as_filled:
            try:
                await maybe_complete_structure(sessionmaker_factory, structure_id)
            except Exception:
                logger.exception("reaper_complete_failed structure_id=%s", structure_id)

    if reaped:
        logger.info("reaper_pass terminalised=%d stale=%d", reaped, len(stale))
    return reaped


async def reaper_loop(
    sessionmaker_factory: async_sessionmaker[AsyncSession],
    executor: Any,
    *,
    interval_s: float = 30.0,
    tau_stale_s: float = 300.0,
) -> None:
    """Run forever ; one guarded pass every ``interval_s``. Cancellable."""
    logger.info("reaper_loop_started interval=%.1fs tau_stale=%.0fs",
                interval_s, tau_stale_s)
    while True:
        try:
            await reap_stale_orders(
                sessionmaker_factory=sessionmaker_factory,
                executor=executor,
                tau_stale_s=tau_stale_s,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("reaper_cycle_crashed")
        await asyncio.sleep(interval_s)

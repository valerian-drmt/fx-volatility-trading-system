"""Order reaper — terminalise stale orders (liveness / invariant I2, defect D1).

Neither existing loop closes the absorbing edge for a stuck order that IB does
NOT hold: ``stuck_order_watcher`` only *alerts*, and ``order_reconciler`` only
flips a stuck order to *filled* when IB actually holds the matching contract. So
an order IB never fills and never cancels sits in
``submitted``/``acknowledged``/``partially_filled`` forever (the "91h" ghost),
its qty still counting against the close-stacking guard.

The reaper closes that edge. Every ``REAPER_INTERVAL_S`` it takes reapable
orders older than ``tau_stale`` and, **guarded by ``account_is_reporting()``**
(never act on a dead feed — a dead feed is not "IB is flat"), drives the ones IB
does not hold to ``expired``. Orders IB *does* hold are left to
``order_reconciler``'s filled backfill, which stays the single writer of the
``filled`` edge — we never invent a phantom fill here.

Held-contract detection reuses ``order_reconciler._leg_matches_position`` against
the ``open_position`` mirror (the gateway truth ``position_sync`` maintains), the
same signal ``order_reconciler`` uses. Reservation release on expiry is wired in
P2 once ``leg_position.reserved_qty`` exists.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.execution.reaper_policy import (
    REAPABLE_STATES,
    decide_reap,
)
from engines.execution.fills_handler import _lock_for
from engines.execution.ib_heartbeat import mark_loop_ran
from engines.execution.order_reconciler import _leg_matches_position
from engines.execution.settings import get_execution_settings
from persistence.models import (
    OpenPosition,
    StructureOrder,
    TradeEvent,
)
from persistence.order_state import EXPIRED_AT_BROKER, end_order, mark_submitted

logger = logging.getLogger(__name__)

REAPER_INTERVAL_S = get_execution_settings().REAPER_INTERVAL_S
REAPER_TAU_STALE_S = get_execution_settings().REAPER_TAU_STALE_S

# IB order statuses that mean the order is still WORKING. A resting limit that
# hasn't filled (e.g. a thin OTM wing) is neither held nor dead — the reaper must
# leave it alone (spec §6.2 `if at_ib: continue`).
# "Inactive" is an order IB HOLDS (outside trading hours, a price cap): it is
# at the broker and can fill when the hold lifts. "PendingCancel" is a cancel
# asked and not yet confirmed. Neither was in this list, so a held order was
# expired after five minutes and its reservation freed while it could still
# fill (#485).
_LIVE_IB_STATUSES = frozenset(
    {"Submitted", "PreSubmitted", "PendingSubmit", "ApiPending", "Inactive", "PendingCancel"}
)


_ORDER_REF_PREFIX = "fxvol:"


def parse_order_ref(ref: str | None) -> tuple[int, int] | None:
    """Parse a live-submit idempotency key ``fxvol:{structure_id}:{order_id}``
    into ``(structure_id, order_id)``; None for anything else. Pure/testable."""
    if not ref or not ref.startswith(_ORDER_REF_PREFIX):
        return None
    parts = ref.split(":")
    if len(parts) != 3:
        return None
    try:
        return int(parts[1]), int(parts[2])
    except ValueError:
        return None


def live_ib_order_keys(ib_trades: list[dict[str, Any]]) -> set[str]:
    """Order ids (+ perm ids) of trades still working at IB. Pure/testable: the
    reaper skips any DB order whose ib_order_id or ib_perm_id is in this set."""
    keys: set[str] = set()
    for t in ib_trades:
        if t.get("status") in _LIVE_IB_STATUSES and float(t.get("remaining") or 0) > 0:
            for field in ("order_id", "perm_id"):
                val = t.get(field)
                if val is not None:
                    keys.add(str(val))
    return keys


async def reap_stale_orders(
    sm: async_sessionmaker[AsyncSession],
    executor: Any,
    *,
    tau_stale_s: float = REAPER_TAU_STALE_S,
    now: datetime | None = None,
) -> dict[str, Any]:
    """One reaper pass. Returns a small summary for logging / the endpoint."""
    # Dead-feed guard (T7): an empty/absent IB snapshot when the feed is down is
    # NOT "IB is flat" — acting on it would fabricate expirations.
    if not executor.account_is_reporting():
        return {"reaped": 0, "expired": [], "skipped": "account_not_reporting"}

    now = now or datetime.now(UTC)
    cutoff = now - timedelta(seconds=tau_stale_s)
    expired_ids: list[int] = []

    # One IB snapshot serves both the adoption sweep and the liveness check.
    # If we can't get it, do NOT act this cycle: never expire an order we
    # can't prove is dead.
    try:
        ib_trades = await executor.list_all_trades()
    except Exception:
        logger.warning("reaper_skip_ib_trades_unavailable")
        return {"reaped": 0, "expired": [], "skipped": "ib_trades_unavailable"}

    async with sm() as db:
        # orderRef adoption sweep (EXEC-2): re-attach ghosts left by a crash
        # between placeOrder and the per-leg commit before judging staleness.
        adopted_ids = await _adopt_orphaned_orders(db, ib_trades, now)

        # Rows that never left (#490). An order still ``pending`` after tau
        # was written by the api and never placed (the engine was unreachable,
        # refused, or the call timed out). The adoption sweep just above has
        # already picked up any such row whose order IS at IB, by its
        # reference; what is left holds nothing at the broker. No loop used
        # to read pending rows: they waited for ever and kept their leg
        # reserved.
        never_placed = (await db.execute(
            select(StructureOrder)
            .where(StructureOrder.state == "pending")
            .where(StructureOrder.ib_order_id.is_(None))
            .where(StructureOrder.state_updated_at < cutoff)
        )).scalars().all()
        never_placed_ids: list[int] = []
        for o in never_placed:
            db.add(TradeEvent(
                structure_id=int(o.structure_id), order_id=int(o.id),
                event_type="order_never_placed", severity="warning",
                description=f"order {o.id} still pending after {tau_stale_s:.0f}s and not at IB -> expired",
                payload={"order_id": int(o.id), "order_role": o.order_role},
            ))
            # under the order's lock, on its state NOW (#485): the adoption
            # or a late answer of the engine may have moved it since the select
            async with _lock_for(int(o.id)):
                await db.refresh(o)
                if o.state == "pending" and o.ib_order_id is None and await end_order(
                        db, o, "expired", reason="never placed at the broker", now=now):
                    never_placed_ids.append(int(o.id))

        stale = (await db.execute(
            select(StructureOrder)
            .where(StructureOrder.state.in_(tuple(REAPABLE_STATES)))
            .where(StructureOrder.submitted_at.is_not(None))
            .where(StructureOrder.submitted_at < cutoff)
        )).scalars().all()
        if not stale:
            if adopted_ids or never_placed_ids:
                await db.commit()
            return {"reaped": len(never_placed_ids), "expired": never_placed_ids, "adopted": adopted_ids}

        # Which of our orders are still WORKING at IB? A resting limit that hasn't
        # filled is neither held nor dead — leave it (spec §6.2 `if at_ib`).
        live_at_ib = live_ib_order_keys(ib_trades)

        struct_ids = {int(o.structure_id) for o in stale}
        positions = (await db.execute(
            select(OpenPosition).where(OpenPosition.trade_id.in_(struct_ids))
        )).scalars().all()
        pos_by_struct: dict[int, list[OpenPosition]] = {}
        for p in positions:
            if p.trade_id is not None:
                pos_by_struct.setdefault(int(p.trade_id), []).append(p)

        used: set[int] = set()  # each mirror row claims at most one leg
        for o in stale:
            # Still working at IB -> legitimately resting (e.g. a thin OTM wing
            # that hasn't been hit), not a ghost. Leave it (spec §6.2).
            if (o.ib_order_id and str(o.ib_order_id) in live_at_ib) or (
                o.ib_perm_id and str(o.ib_perm_id) in live_at_ib
            ):
                continue
            cands = pos_by_struct.get(int(o.structure_id), [])
            match = next(
                (p for p in cands if p.id not in used and _leg_matches_position(o, p)),
                None,
            )
            held = match is not None
            submitted_at = o.submitted_at
            if now.tzinfo is not None and submitted_at.tzinfo is None:
                submitted_at = submitted_at.replace(tzinfo=UTC)   # a driver that drops the zone
            age_s = (now - submitted_at).total_seconds()
            target = decide_reap(
                state=o.state, age_s=age_s, tau_s=tau_stale_s,
                held_at_ib=held, matches_contract=held,
            )
            if target == "filled":
                # IB holds it -> order_reconciler owns the filled backfill (single
                # writer for the filled edge). Claim the mirror row so it can't be
                # matched to another leg, then leave the order to the reconciler.
                if match is not None:
                    used.add(match.id)
                continue
            if target == "expired":
                # Event BEFORE the mutation so state_before is captured.
                db.add(TradeEvent(
                    structure_id=int(o.structure_id), order_id=int(o.id),
                    event_type="order_reaped_expired", severity="warning",
                    description=(
                        f"order {o.id} stale in {o.state} >{tau_stale_s:.0f}s "
                        f"and not held at IB -> expired"
                    ),
                    payload={
                        "order_id": int(o.id), "state_before": o.state,
                        "age_seconds": age_s, "ib_order_id": o.ib_order_id,
                    },
                ))
                # The order, the leg it reserved, and its trade, in one place
                # (#489), under the order's own lock and on its state NOW: a
                # fill or a cancel handled since this pass read the row wins
                # (#485). The reaper used to write over it.
                async with _lock_for(int(o.id)):
                    await db.refresh(o)
                    if o.state in REAPABLE_STATES and await end_order(
                            db, o, "expired", now=now,
                            reason=None if o.rejection_text else EXPIRED_AT_BROKER):
                        expired_ids.append(int(o.id))

        await db.commit()

    expired_ids = [*never_placed_ids, *expired_ids]
    if expired_ids:
        logger.warning("order_reaper expired=%s", expired_ids)
    return {"reaped": len(expired_ids), "expired": expired_ids, "adopted": adopted_ids}


async def _adopt_orphaned_orders(
    db: AsyncSession, ib_trades: list[dict[str, Any]], now: datetime,
) -> list[int]:
    """Adopt live IB orders whose DB rows never got their ids (EXEC-2).

    A crash between ``placeOrder`` and the per-leg commit leaves an order live
    at IB while its row still says ``pending`` with no ``ib_order_id`` —
    invisible to the reaper and the stuck-watcher. live_submit stamps
    ``orderRef='fxvol:{structure_id}:{order_id}'`` (persisted by IB) on every
    order, so such ghosts are re-attached here instead of staying orphans or
    being double-placed."""
    ref_map: dict[int, dict[str, Any]] = {}
    for t in ib_trades:
        parsed = parse_order_ref(t.get("order_ref"))
        if parsed is not None:
            ref_map[parsed[1]] = t
    if not ref_map:
        return []
    rows = (await db.execute(
        select(StructureOrder)
        .where(StructureOrder.id.in_(tuple(ref_map)))
        .where(StructureOrder.ib_order_id.is_(None))
    )).scalars().all()
    adopted: list[int] = []
    for o in rows:
        t = ref_map[int(o.id)]
        await mark_submitted(
            db, o, now=now,
            ib_order_id=str(t["order_id"]) if t.get("order_id") is not None else None,
            ib_perm_id=str(t["perm_id"]) if t.get("perm_id") is not None else None,
        )
        db.add(TradeEvent(
            structure_id=int(o.structure_id), order_id=int(o.id),
            event_type="order_adopted_from_ib", severity="warning",
            description=(
                f"order {o.id} adopted from IB via orderRef "
                "(ghost after mid-submit crash)"
            ),
            payload={
                "order_id": int(o.id), "ib_order_id": o.ib_order_id,
                "order_ref": t.get("order_ref"),
            },
        ))
        adopted.append(int(o.id))
    if adopted:
        logger.warning("reaper_adopted_orphans ids=%s", adopted)
    return adopted


async def reaper_loop(
    sm: async_sessionmaker[AsyncSession],
    executor: Any,
    *,
    interval_s: float = REAPER_INTERVAL_S,
    tau_stale_s: float = REAPER_TAU_STALE_S,
) -> None:
    """Run forever; reap every ``interval_s``. Cancellable via task.cancel()."""
    while True:
        try:
            await reap_stale_orders(sm, executor, tau_stale_s=tau_stale_s)
            mark_loop_ran("reaper")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("reaper_loop_error")
        await asyncio.sleep(interval_s)

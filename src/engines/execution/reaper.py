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
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.execution.reaper_policy import (
    REAPABLE_STATES,
    decide_reap,
    plan_structure_terminal_state,
)
from engines.execution.order_reconciler import _leg_matches_position
from persistence.models import (
    OpenPosition,
    StructureOrder,
    TradeEvent,
    TradeStructure,
)
from persistence.reservation import recompute_reservation

logger = logging.getLogger(__name__)

REAPER_INTERVAL_S = float(os.getenv("REAPER_INTERVAL_S", "30.0"))
REAPER_TAU_STALE_S = float(os.getenv("REAPER_TAU_STALE_S", "300.0"))

# IB order statuses that mean the order is still WORKING. A resting limit that
# hasn't filled (e.g. a thin OTM wing) is neither held nor dead — the reaper must
# leave it alone (spec §6.2 `if at_ib: continue`).
_LIVE_IB_STATUSES = frozenset(
    {"Submitted", "PreSubmitted", "PendingSubmit", "ApiPending"}
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

        stale = (await db.execute(
            select(StructureOrder)
            .where(StructureOrder.state.in_(tuple(REAPABLE_STATES)))
            .where(StructureOrder.submitted_at.is_not(None))
            .where(StructureOrder.submitted_at < cutoff)
        )).scalars().all()
        if not stale:
            if adopted_ids:
                await db.commit()
            return {"reaped": 0, "expired": [], "adopted": adopted_ids}

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
            age_s = (now - o.submitted_at).total_seconds()
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
                o.state = "expired"
                o.state_updated_at = now
                expired_ids.append(int(o.id))
                # Release the reservation this dead close was holding (I5, spec §6.2).
                if o.order_role == "closing" and o.closes_order_id is not None:
                    await recompute_reservation(db, entry_order_id=o.closes_order_id)

        # Propagate to the STRUCTURE FSM (D1 at the structure level). Expiring a
        # leg can make every order of its structure terminal — but the happy path
        # (fills_handler) only ever set 'fully_filled' when ALL legs filled, so a
        # structure with an expired/never-filled leg would sit 'submitted' forever.
        # Terminalise any affected structure whose orders are now all terminal.
        for sid in {int(o.structure_id) for o in stale if int(o.id) in expired_ids}:
            all_states = (await db.execute(
                select(StructureOrder.state).where(StructureOrder.structure_id == sid)
            )).scalars().all()
            new_state = plan_structure_terminal_state(list(all_states))
            if new_state is None:
                continue
            struct = await db.get(TradeStructure, sid)
            if struct is not None and struct.state != new_state:
                struct.state = new_state
                struct.state_updated_at = now
                db.add(TradeEvent(
                    structure_id=sid, order_id=None,
                    event_type="structure_terminalised", severity="info",
                    description=f"structure {sid} -> {new_state} (all legs terminal)",
                    payload={"structure_id": sid, "new_state": new_state},
                ))

        await db.commit()

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
        o.ib_order_id = str(t["order_id"]) if t.get("order_id") is not None else None
        o.ib_perm_id = str(t["perm_id"]) if t.get("perm_id") is not None else None
        if o.state == "pending":
            o.state = "submitted"
            o.state_updated_at = now
        if o.submitted_at is None:
            o.submitted_at = now
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
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("reaper_loop_error")
        await asyncio.sleep(interval_s)

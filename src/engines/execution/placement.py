"""One way to place an order at IB: claim the row, stamp the key, place, stamp
the ids (#318, audit 04#1 / EX-11 / EX-15 / EX-23 / EX-24).

Only the structure entry path (``live_submit``) had an atomic claim and an
``orderRef``. Hedges, rollback unwinds, closes and manual orders went to IB
with neither, so a retried request could place the same order twice and an
orphan at IB could not be matched back to its row. Every path now goes
through :func:`place_with_claim`:

1. ``claim_submit``: ``UPDATE <table> SET submit_claimed_at = now()
   WHERE id = :id AND submit_claimed_at IS NULL AND ib_order_id IS NULL``,
   committed in its own transaction BEFORE anything touches IB. A replay
   loses the race and gets :class:`AlreadyClaimed`.
2. ``Order.orderRef`` = a durable key IB persists and returns on open-order
   and execution queries (``fxvol:<structure>:<order>`` for structure legs
   so the reaper's adoption sweep matches them, ``fxvol:hedge:<id>``,
   ``fxvol:manual:<token>``).
3. ``ib.placeOrder``. If it raises, nothing reached IB: the claim is
   released so a genuine retry stays possible.
4. ``stamp_placement``: ids + ``submitted_at`` + state, committed.

Callers must NOT hold an open transaction with uncommitted writes on the
row when they call this (Postgres would block the claim UPDATE on their own
row lock): commit first, claim second.
"""
from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from engines.execution.latency import note_submit_start

logger = logging.getLogger(__name__)


class AlreadyClaimed(RuntimeError):
    """The row was already claimed for placement (or already carries an IB id)."""

    def __init__(self, table: str, row_id: int) -> None:
        self.table, self.row_id = table, row_id
        super().__init__(f"{table} {row_id} already claimed for placement — refusing to re-place")


def hedge_order_ref(hedge_order_id: int) -> str:
    return f"fxvol:hedge:{hedge_order_id}"


def manual_order_ref() -> str:
    """Operator one-shots have no row to claim; they still get a key IB keeps,
    echoed in the response so the order can be found again."""
    return f"fxvol:manual:{secrets.token_hex(6)}"


async def claim_submit(sm: async_sessionmaker[AsyncSession], model: Any, row_id: int) -> bool:
    """Atomic claim, committed. True when this caller owns the placement."""
    async with sm() as db:
        got = (await db.execute(
            update(model)
            .where(
                model.id == row_id,
                model.submit_claimed_at.is_(None),
                model.ib_order_id.is_(None),
            )
            .values(submit_claimed_at=datetime.now(UTC))
            .returning(model.id)
        )).scalar_one_or_none()
        await db.commit()
    return got is not None


async def release_submit_claim(sm: async_sessionmaker[AsyncSession], model: Any, row_id: int) -> None:
    """Undo the claim after a failure that placed NOTHING at IB."""
    async with sm() as db:
        await db.execute(
            update(model).where(model.id == row_id).values(submit_claimed_at=None)
        )
        await db.commit()


async def stamp_placement(
    sm: async_sessionmaker[AsyncSession], model: Any, row_id: int, trade: Any,
    *, state: str = "submitted",
) -> None:
    """Record what IB handed back: orderId, permId (when the model has it),
    ``submitted_at``, ``state``."""
    o = getattr(trade, "order", None)
    values: dict[str, Any] = {
        "ib_order_id": str(getattr(o, "orderId", "") or "") or None,
        "submitted_at": datetime.now(UTC),
        "state": state,
    }
    if hasattr(model, "ib_perm_id"):
        perm = getattr(o, "permId", None)
        values["ib_perm_id"] = str(perm) if perm else None
    if hasattr(model, "state_updated_at"):
        values["state_updated_at"] = datetime.now(UTC)
    async with sm() as db:
        await db.execute(update(model).where(model.id == row_id).values(**values))
        await db.commit()


async def place_with_claim(
    *,
    sm: async_sessionmaker[AsyncSession],
    model: Any,
    row_id: int,
    order_ref: str,
    ib: Any,
    contract: Any,
    order: Any,
    state_after: str = "submitted",
) -> Any:
    """Claim → orderRef → placeOrder → stamp. Returns the ib_insync Trade."""
    if not await claim_submit(sm, model, row_id):
        raise AlreadyClaimed(getattr(model, "__tablename__", str(model)), row_id)
    order.orderRef = order_ref
    note_submit_start((str(getattr(model, '__tablename__', model.__name__)), int(row_id)))
    try:
        trade = ib.placeOrder(contract, order)
    except Exception:
        # placeOrder raised before handing the order to the socket: nothing is
        # live at IB, so a retry must be allowed.
        await release_submit_claim(sm, model, row_id)
        logger.exception("place_with_claim_failed table=%s id=%s ref=%s",
                         getattr(model, "__tablename__", "?"), row_id, order_ref)
        raise
    await stamp_placement(sm, model, row_id, trade, state=state_after)
    logger.info("order_placed table=%s id=%s ref=%s ib_order_id=%s",
                getattr(model, "__tablename__", "?"), row_id, order_ref,
                getattr(getattr(trade, "order", None), "orderId", None))
    return trade

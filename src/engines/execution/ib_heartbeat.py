"""Heartbeat + stuck-order watcher loops for execution-engine.

Two background tasks owned by `engines.execution.main:lifespan` :

1. ``heartbeat_loop`` — every ``HEARTBEAT_INTERVAL_S`` (default 10 s) :
   * Read account summary + connection state from ``OrderExecutor``.
   * UPDATE the singleton ``runtime_ib_session`` row (id=1) with
     ``is_connected``, ``last_heartbeat``, ``available_funds_usd``,
     ``buying_power_usd``, ``margin_used_usd``.
   * If the state flips connected→disconnected, increment ``n_disconnects_24h``
     and stamp ``last_disconnect_at``.

2. ``stuck_order_watcher_loop`` — every ``STUCK_WATCH_INTERVAL_S`` (default
   60 s) : pick all ``structure_orders`` rows still in
   ``state ∈ ('submitted','acknowledged')`` whose ``submitted_at`` is older
   than ``stuck_after_seconds`` (default 600 s = 10 min). For each, log a
   single ``execution_audit_log`` row at severity=critical. **No auto-cancel
   in V1** (spec §7.4) — operator decides.

Spec : ``docs/vol_trading_pca/specs/STEP4_EXECUTION.md`` §7.4 + DoD §12.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from persistence.models import IbConnectionState, StructureOrder, TradeEvent

logger = logging.getLogger(__name__)


_NUMERIC_TAGS = ("AvailableFunds", "BuyingPower", "MaintMarginReq")


def _pick_float(account_summary: dict[str, Any], tag: str) -> float | None:
    """Defensive numeric extraction from OrderExecutor.account_summary() output."""
    v = account_summary.get(tag)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


async def update_heartbeat_row(
    db: AsyncSession,
    *,
    is_connected: bool,
    account_summary: dict[str, Any] | None,
    now: datetime,
) -> None:
    """Single-row UPDATE on ``runtime_ib_session``. Caller commits."""
    row = (await db.execute(
        select(IbConnectionState).where(IbConnectionState.broker == "IB").limit(1)
    )).scalar_one_or_none()
    if row is None:
        # Migration 015 seeds it ; if missing we re-create rather than crash.
        row = IbConnectionState(broker="IB", is_connected=False, last_heartbeat=now)
        db.add(row)
        await db.flush()

    was_connected = bool(row.is_connected)
    row.is_connected = is_connected
    row.last_heartbeat = now
    if is_connected and account_summary is not None:
        acct_id = account_summary.get("account") or row.account_id
        row.account_id = acct_id
        # IB account-id prefix convention : 'D' = paper (e.g. DU1234567),
        # 'U' = live retail (e.g. U1234567), 'F' = live institutional.
        # Fall back to keeping the previous value if the prefix is unknown.
        if acct_id:
            first = str(acct_id)[:1].upper()
            if first == "D":
                row.account_type = "paper"
            elif first in ("U", "F"):
                row.account_type = "live"
        row.available_funds_usd = _pick_float(account_summary, "AvailableFunds")
        row.buying_power_usd = _pick_float(account_summary, "BuyingPower")
        row.margin_used_usd = _pick_float(account_summary, "MaintMarginReq")
    if was_connected and not is_connected:
        row.last_disconnect_at = now
        row.n_disconnects_24h = (row.n_disconnects_24h or 0) + 1


async def heartbeat_loop(
    sessionmaker_factory: async_sessionmaker[AsyncSession],
    executor: Any,
    *,
    interval_s: float = 10.0,
) -> None:
    """Run forever. Cancellable via task.cancel()."""
    while True:
        try:
            now = datetime.now(UTC)
            connected = bool(executor.is_connected())
            summary: dict[str, Any] | None = None
            if connected:
                try:
                    summary = await executor.account_summary()
                except Exception:
                    logger.exception("heartbeat_account_summary_failed")
                    summary = None
            async with sessionmaker_factory() as db:
                await update_heartbeat_row(
                    db, is_connected=connected, account_summary=summary, now=now,
                )
                await db.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("heartbeat_cycle_crashed")
        await asyncio.sleep(interval_s)


# --------------------------------------------------------------------------
# Stuck-order watcher (spec §7.4 — alert only, no auto-cancel V1)
# --------------------------------------------------------------------------

_STUCK_STATES = ("submitted", "acknowledged")
_STUCK_AUDIT_EVENT = "stuck_order_alert"


async def find_stuck_orders(
    db: AsyncSession, *, now: datetime, stuck_after_seconds: float,
) -> list[StructureOrder]:
    cutoff = now - timedelta(seconds=stuck_after_seconds)
    rows = (await db.execute(
        select(StructureOrder)
        .where(StructureOrder.state.in_(_STUCK_STATES))
        .where(StructureOrder.submitted_at.is_not(None))
        .where(StructureOrder.submitted_at < cutoff)
    )).scalars().all()
    return list(rows)


async def _already_alerted_recently(
    db: AsyncSession, order_id: int, now: datetime, dedup_window_s: float,
) -> bool:
    """Avoid spamming audit log : one alert per order per dedup_window."""
    cutoff = now - timedelta(seconds=dedup_window_s)
    existing = (await db.execute(
        select(TradeEvent.id)
        .where(TradeEvent.event_type == _STUCK_AUDIT_EVENT)
        .where(TradeEvent.order_id == order_id)
        .where(TradeEvent.ts >= cutoff)
        .limit(1)
    )).scalar_one_or_none()
    return existing is not None


async def stuck_order_watcher_loop(
    sessionmaker_factory: async_sessionmaker[AsyncSession],
    *,
    interval_s: float = 60.0,
    stuck_after_seconds: float = 600.0,
    dedup_window_s: float = 600.0,
) -> None:
    while True:
        try:
            now = datetime.now(UTC)
            async with sessionmaker_factory() as db:
                stuck = await find_stuck_orders(
                    db, now=now, stuck_after_seconds=stuck_after_seconds,
                )
                for order in stuck:
                    if await _already_alerted_recently(
                        db, order.id, now, dedup_window_s,
                    ):
                        continue
                    age_s = (now - order.submitted_at).total_seconds() if order.submitted_at else 0.0
                    db.add(TradeEvent(
                        structure_id=order.structure_id,
                        order_id=order.id,
                        event_type=_STUCK_AUDIT_EVENT,
                        severity="critical",
                        description=(
                            f"order {order.id} stuck in {order.state} "
                            f"for {age_s:.0f}s (>{stuck_after_seconds:.0f}s)"
                        ),
                        payload={
                            "order_id": order.id,
                            "structure_id": order.structure_id,
                            "state": order.state,
                            "age_seconds": age_s,
                            "ib_order_id": order.ib_order_id,
                        },
                    ))
                    logger.warning(
                        "stuck_order_alert order_id=%s structure_id=%s age_s=%.0f",
                        order.id, order.structure_id, age_s,
                    )
                await db.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("stuck_order_watcher_cycle_crashed")
        await asyncio.sleep(interval_s)


# --------------------------------------------------------------------------
# Gating helper (used by api / submit flow via the cached state row)
# --------------------------------------------------------------------------

async def fetch_ib_connected(db: AsyncSession) -> bool:
    """Return latest cached value of ``runtime_ib_session.is_connected``.

    Submit flow uses this as a pre-condition (cf. spec §7.4 — "Gating Submit
    on is_connected"). Cheap : single-row PK lookup.
    """
    row = (await db.execute(
        select(IbConnectionState).where(IbConnectionState.broker == "IB").limit(1)
    )).scalar_one_or_none()
    if row is None:
        return False
    return bool(row.is_connected)


async def mark_disconnected(db: AsyncSession, now: datetime) -> None:
    """Force-flip the row to disconnected (used when execution-engine shuts down)."""
    await db.execute(
        update(IbConnectionState)
        .where(IbConnectionState.broker == "IB")
        .values(is_connected=False, last_heartbeat=now, last_disconnect_at=now)
    )

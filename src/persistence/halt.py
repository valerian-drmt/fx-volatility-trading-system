"""The kill switch, durably (#315, audit 04#13).

One row in ``config_scalar`` (namespace ``risk``, name ``trading_halted``,
value 1.0 / 0.0) is the authority. Postgres holds it, so it survives a Redis
restart and every service that can place an order (api, execution engine)
reads the same bit. Each flip writes a ``trade_event`` so the audit trail
says who halted, when and why. ``bus.halt`` mirrors the state to Redis for
cheap reads by the UI and publishes the alert.

``is_halted`` raises on a DB error on purpose: callers treat "cannot read the
flag" as halted (fail closed).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from persistence.models import AppConfigScalar, TradeEvent

NAMESPACE = "risk"
NAME = "trading_halted"


@dataclass(frozen=True)
class HaltState:
    halted: bool
    reason: str | None
    set_by: str | None
    set_at: datetime | None


async def is_halted(db: AsyncSession) -> bool:
    row = (await db.execute(
        select(AppConfigScalar.value)
        .where(AppConfigScalar.namespace == NAMESPACE, AppConfigScalar.name == NAME)
        .limit(1)
    )).scalar_one_or_none()
    return bool(row is not None and float(row) >= 1.0)


async def get_halt(db: AsyncSession) -> HaltState:
    halted = await is_halted(db)
    last = (await db.execute(
        select(TradeEvent)
        .where(TradeEvent.event_type.in_(("trading_halted", "trading_resumed")))
        .order_by(TradeEvent.id.desc())
        .limit(1)
    )).scalar_one_or_none()
    if last is None or not halted:
        return HaltState(halted, None, None, None)
    payload = last.payload or {}
    return HaltState(
        halted, str(payload.get("reason") or "") or None, str(payload.get("by") or "") or None,
        getattr(last, "ts", None),
    )


async def set_halt(db: AsyncSession, *, halted: bool, reason: str, by: str) -> HaltState:
    """Flip the flag and write the audit event. Caller commits."""
    row = (await db.execute(
        select(AppConfigScalar)
        .where(AppConfigScalar.namespace == NAMESPACE, AppConfigScalar.name == NAME)
        .limit(1)
    )).scalar_one_or_none()
    if row is None:
        row = AppConfigScalar(namespace=NAMESPACE, name=NAME, value=1.0 if halted else 0.0)
        for attr, val in (("is_active", True), ("description", "kill switch: 1 = no new orders")):
            if hasattr(row, attr):
                setattr(row, attr, val)
        db.add(row)
    else:
        row.value = 1.0 if halted else 0.0
    now = datetime.now(UTC)
    db.add(TradeEvent(
        structure_id=None,
        event_type="trading_halted" if halted else "trading_resumed",
        severity="error" if halted else "warning",
        description=(f"TRADING HALTED by {by}: {reason}" if halted else f"trading resumed by {by}: {reason}")[:500],
        payload={"reason": reason[:300], "by": by[:60], "at": now.isoformat()},
    ))
    return HaltState(halted, reason, by, now)

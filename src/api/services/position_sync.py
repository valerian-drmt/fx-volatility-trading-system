"""Sync IB live positions → Postgres `positions` + insert `position_snapshots`.

Pipeline :
  - **sync_positions_from_ib** : fait l'upsert IB → DB. Match par tuple
    (symbol, instrument_type, strike, maturity, option_type) — pas besoin
    d'ajouter une colonne con_id à Position.
  - **insert_snapshots** : pour chaque OPEN position, insère un row dans
    position_snapshots avec qty, avg_cost, timestamp. Greeks à null
    (calculés à partir de surface dans une PR future).

Lance au startup api + via un loop périodique (30s).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.services.order_executor import OrderExecutor
from persistence.models import Position, PositionSnapshot

logger = logging.getLogger(__name__)

SNAPSHOT_INTERVAL_S = 30.0


def _sec_type_to_instrument_type(sec_type: str) -> str:
    return {"FUT": "FUTURE", "CONTFUT": "FUTURE", "OPT": "OPTION", "FOP": "OPTION", "STK": "SPOT"}.get(sec_type, "SPOT")


def _right_to_option_type(right: str | None) -> str | None:
    if not right:
        return None
    return {"C": "CALL", "P": "PUT", "CALL": "CALL", "PUT": "PUT"}.get(right)


def _expiry_to_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y%m%d").date()
    except ValueError:
        return None


def _ib_position_key(p: dict) -> tuple:
    """Identité unique d'une position pour upsert."""
    return (
        p.get("symbol"),
        _sec_type_to_instrument_type(p.get("sec_type", "")),
        Decimal(str(p["strike"])) if p.get("strike") else None,
        _expiry_to_date(p.get("expiry")),
        _right_to_option_type(p.get("right")),
    )


def _db_position_key(p: Position) -> tuple:
    return (
        p.symbol,
        p.instrument_type,
        p.strike,
        p.maturity,
        p.option_type,
    )


async def sync_positions_from_ib(db: AsyncSession, executor: OrderExecutor) -> dict:
    """Upsert IB positions in DB. Close DB rows that no longer exist in IB.

    Returns ``{synced: N, opened: M, closed: K, unchanged: J}``.
    """
    if not executor.is_connected():
        return {"synced": 0, "opened": 0, "closed": 0, "unchanged": 0, "error": "ib_not_connected"}

    ib_positions_raw = await executor.list_positions()
    ib_active = [p for p in ib_positions_raw if abs(p.get("position", 0)) > 0]
    ib_by_key = {_ib_position_key(p): p for p in ib_active}

    db_rows = (await db.execute(
        select(Position).where(Position.status == "OPEN")
    )).scalars().all()
    db_by_key = {_db_position_key(p): p for p in db_rows}

    now = datetime.now(UTC)
    opened = 0
    unchanged = 0
    for key, ib_pos in ib_by_key.items():
        qty = abs(Decimal(str(ib_pos["position"])))
        side = "BUY" if ib_pos["position"] > 0 else "SELL"
        avg_cost = Decimal(str(ib_pos.get("avg_cost", 0)))
        if key in db_by_key:
            row = db_by_key[key]
            # Update qty if changed (partial fill, addition).
            if row.quantity != qty or row.side != side:
                row.quantity = qty
                row.side = side
                row.entry_price = avg_cost  # avg_cost en monnaie totale ; à diviser par multiplier en post-prod
            else:
                unchanged += 1
        else:
            symbol, instrument_type, strike, maturity, option_type = key
            row = Position(
                symbol=symbol or "",
                instrument_type=instrument_type,
                side=side,
                quantity=qty,
                strike=strike,
                maturity=maturity,
                option_type=option_type,
                entry_price=avg_cost,
                entry_timestamp=now,
                status="OPEN",
            )
            db.add(row)
            opened += 1

    # Close DB rows that no longer have an IB counterpart (= position fermée
    # côté broker, on n'avait juste pas vu la fermeture).
    closed = 0
    for key, db_row in db_by_key.items():
        if key not in ib_by_key:
            db_row.status = "CLOSED"
            db_row.exit_timestamp = now
            closed += 1

    await db.commit()
    return {"synced": len(ib_active), "opened": opened, "closed": closed, "unchanged": unchanged}


async def insert_snapshots(db: AsyncSession, executor: OrderExecutor) -> int:
    """Insert one PositionSnapshot per OPEN position. Returns rows inserted."""
    if not executor.is_connected():
        return 0
    db_rows = (await db.execute(
        select(Position).where(Position.status == "OPEN")
    )).scalars().all()
    if not db_rows:
        return 0

    # Pour le snapshot, on prend juste le qty + avg_cost ; pas de greeks
    # calculés ici (viendrait d'une PR séparée qui lit la surface vol).
    now = datetime.now(UTC)
    inserted = 0
    for row in db_rows:
        snap = PositionSnapshot(
            position_id=row.id,
            timestamp=now,
            spot=None,         # à brancher : latest_spot:EURUSD depuis Redis
            iv=None,
            delta_usd=None,
            vega_usd=None,
            gamma_usd=None,
            theta_usd=None,
            pnl_usd=None,
        )
        db.add(snap)
        inserted += 1
    await db.commit()
    return inserted


async def position_sync_loop(
    session_maker: async_sessionmaker[AsyncSession],
    executor: OrderExecutor,
    interval_s: float = SNAPSHOT_INTERVAL_S,
) -> None:
    """Background asyncio task : sync + snapshot toutes les `interval_s` secondes.

    Lancé depuis le lifespan FastAPI. Best-effort : log les exceptions sans
    crash (sinon le task disparaît silencieusement).
    """
    logger.info("position_sync_loop_started interval=%.1fs", interval_s)
    # Initial sync immédiat (ne pas attendre 30s pour voir des rows).
    try:
        async with session_maker() as db:
            sync = await sync_positions_from_ib(db, executor)
            logger.info("position_sync_initial %s", sync)
    except Exception:
        logger.exception("position_sync_initial_failed")

    while True:
        try:
            await asyncio.sleep(interval_s)
            async with session_maker() as db:
                sync = await sync_positions_from_ib(db, executor)
                snaps = await insert_snapshots(db, executor)
                logger.info("position_sync_tick sync=%s snapshots=%s", sync, snaps)
        except asyncio.CancelledError:
            logger.info("position_sync_loop_cancelled")
            raise
        except Exception:
            logger.exception("position_sync_tick_failed")

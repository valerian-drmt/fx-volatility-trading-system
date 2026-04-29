"""Sync IB live positions → Postgres `positions` + insert `position_snapshots`.

Pipeline :
  - **sync_positions_from_ib** : fait l'upsert IB → DB. Match par tuple
    (symbol, instrument_type, strike, maturity, option_type) — pas besoin
    d'ajouter une colonne con_id à Position.
  - **insert_snapshots** : pour chaque OPEN position, insère un row dans
    position_snapshots avec spot, iv, greeks (BSM pour OPT, lin pour FUT),
    et P&L unrealized.

Lance au startup api + via un loop périodique (30s).
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, date, datetime
from decimal import Decimal

from redis import asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from services.execution.order_executor import OrderExecutor
from core.pricing.bs import bs_delta, bs_gamma, bs_theta, bs_vega
from persistence.models import AccountSnap, Order, Position, PositionSnapshot, Trade

logger = logging.getLogger(__name__)

SNAPSHOT_INTERVAL_S = 30.0

# Multiplier hardcodé pour EUR FX futures + options (CME). Une vraie impl
# utiliserait `ib.qualifyContractsAsync` pour lire contract.multiplier.
EUR_MULTIPLIER = Decimal("125000")


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
    return (
        p.get("symbol"),
        _sec_type_to_instrument_type(p.get("sec_type", "")),
        Decimal(str(p["strike"])) if p.get("strike") else None,
        _expiry_to_date(p.get("expiry")),
        _right_to_option_type(p.get("right")),
    )


def _db_position_key(p: Position) -> tuple:
    return (p.symbol, p.instrument_type, p.strike, p.maturity, p.option_type)


async def _read_spot_from_redis(redis: aioredis.Redis | None, symbol: str = "EURUSD") -> float | None:
    if redis is None:
        return None
    try:
        raw = await redis.get(f"latest_spot:{symbol}")
    except Exception:
        return None
    if raw is None:
        return None
    text = raw.decode() if isinstance(raw, bytes) else raw
    try:
        return float(text)
    except ValueError:
        try:
            payload = json.loads(text)
            return float(payload.get("mid") or payload.get("bid"))
        except (ValueError, TypeError, AttributeError):
            return None


async def _read_atm_iv_from_redis(redis: aioredis.Redis | None, tenor_days: int) -> float | None:
    """Closest ATM IV from latest_vol_surface (best-effort, retourne None
    si surface absente ou tenor pas trouvé). IV en décimal (0.06 = 6%)."""
    if redis is None:
        return None
    try:
        raw = await redis.get("latest_vol_surface:EURUSD")
    except Exception:
        return None
    if raw is None:
        return None
    try:
        payload = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
        surface = payload.get("surface", {})
    except (ValueError, TypeError):
        return None
    # Pick closest tenor by DTE.
    tenor_to_days = {"1M": 30, "2M": 60, "3M": 90, "4M": 120, "5M": 150, "6M": 180}
    best_tenor = min(tenor_to_days.keys(), key=lambda t: abs(tenor_to_days[t] - tenor_days))
    pillar = surface.get(best_tenor) or {}
    atm = pillar.get("atm") if isinstance(pillar, dict) else None
    if isinstance(atm, dict) and isinstance(atm.get("iv"), (int, float)):
        return float(atm["iv"])
    return None


async def sync_positions_from_ib(
    db: AsyncSession,
    executor: OrderExecutor,
    redis: aioredis.Redis | None = None,
) -> dict:
    """Upsert IB positions in DB. Close DB rows that no longer exist in IB."""
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
            if row.quantity != qty or row.side != side:
                row.quantity = qty
                row.side = side
                row.entry_price = avg_cost
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

    # Close DB rows that no longer have an IB counterpart. Le timestamp et
    # le price de fermeture sont disponibles dans le dernier trade lié à
    # cette position (cf. table `trades`).
    closed = 0
    for key, db_row in db_by_key.items():
        if key not in ib_by_key:
            db_row.status = "CLOSED"
            closed += 1

    await db.commit()
    return {"synced": len(ib_active), "opened": opened, "closed": closed, "unchanged": unchanged}


def _compute_position_metrics(
    pos: Position,
    spot: float | None,
    iv: float | None,
) -> dict:
    """Calcule spot/iv/greeks/pnl pour un Position. Retourne dict de Decimals."""
    out: dict = {"spot": None, "iv": None, "delta_usd": None, "vega_usd": None,
                 "gamma_usd": None, "theta_usd": None, "pnl_usd": None}
    if spot is None:
        return out
    out["spot"] = Decimal(str(spot))

    qty = float(pos.quantity)
    sign = 1.0 if pos.side == "BUY" else -1.0
    mult = float(EUR_MULTIPLIER)
    # entry_price stocke avg_cost = unit_price × multiplier ⇒ unit_price = entry_price / mult.
    unit_entry = float(pos.entry_price) / mult if pos.entry_price else 0.0

    if pos.instrument_type == "FUTURE":
        # Future : delta = ±1 par contrat, gamma/vega/theta = 0.
        out["delta_usd"] = Decimal(str(round(sign * qty * mult, 2)))
        out["gamma_usd"] = Decimal("0")
        out["vega_usd"] = Decimal("0")
        out["theta_usd"] = Decimal("0")
        pnl = (spot - unit_entry) * qty * mult * sign
        out["pnl_usd"] = Decimal(str(round(pnl, 2)))
        return out

    if pos.instrument_type == "OPTION" and iv is not None and pos.strike and pos.maturity:
        K = float(pos.strike)
        right = "C" if pos.option_type == "CALL" else "P"
        T = max(0.001, (pos.maturity - datetime.now(UTC).date()).days / 365.0)
        F = spot
        d = bs_delta(F, K, T, iv, right)
        g = bs_gamma(F, K, T, iv)
        v = bs_vega(F, K, T, iv)
        th = bs_theta(F, K, T, iv, right)
        out["iv"] = Decimal(str(round(iv, 5)))
        out["delta_usd"] = Decimal(str(round(d * sign * qty * mult, 2)))
        out["gamma_usd"] = Decimal(str(round(g * sign * qty * mult, 2)))
        # bs_vega est par 1.0 abs vol → diviser par 100 pour avoir par 1 vol pt.
        out["vega_usd"] = Decimal(str(round(v * sign * qty * mult * 0.01, 2)))
        out["theta_usd"] = Decimal(str(round(th * sign * qty * mult, 2)))
        # Pnl unrealized : mark BS courant − unit_entry, le tout × qty × mult × sign.
        from core.pricing.bs import bs_price
        mark = bs_price(F, K, T, iv, right)
        pnl = (mark - unit_entry) * qty * mult * sign
        out["pnl_usd"] = Decimal(str(round(pnl, 2)))
    return out


async def insert_snapshots(
    db: AsyncSession,
    executor: OrderExecutor,
    redis: aioredis.Redis | None = None,
) -> int:
    """Insert one PositionSnapshot per OPEN position with full metrics."""
    if not executor.is_connected():
        return 0
    db_rows = (await db.execute(
        select(Position).where(Position.status == "OPEN")
    )).scalars().all()
    if not db_rows:
        return 0

    spot = await _read_spot_from_redis(redis)
    now = datetime.now(UTC)
    inserted = 0
    for row in db_rows:
        # Pour les options, lookup ATM IV au tenor le plus proche.
        iv = None
        if row.instrument_type == "OPTION" and row.maturity:
            dte = max(1, (row.maturity - now.date()).days)
            iv = await _read_atm_iv_from_redis(redis, dte)
        m = _compute_position_metrics(row, spot, iv)
        snap = PositionSnapshot(
            position_id=row.id,
            timestamp=now,
            spot=m["spot"],
            iv=m["iv"],
            delta_usd=m["delta_usd"],
            vega_usd=m["vega_usd"],
            gamma_usd=m["gamma_usd"],
            theta_usd=m["theta_usd"],
            pnl_usd=m["pnl_usd"],
        )
        db.add(snap)
        inserted += 1
    await db.commit()
    return inserted


async def sync_orders_from_ib(db: AsyncSession, executor: OrderExecutor) -> dict:
    """Upsert tous les Trade IB (= orders côté nous) dans la table orders.

    Match par `ib_perm_id` (identifiant IB stable cross-session).
    Les statuses suivent IB : PendingSubmit / Submitted / Filled / Cancelled / etc.
    """
    if not executor.is_connected():
        return {"synced": 0, "upserted": 0, "error": "ib_not_connected"}
    ib = executor._ib  # type: ignore[attr-defined]
    trades = ib.trades() if ib else []

    upserted = 0
    for t in trades:
        o = t.order
        c = t.contract
        s = t.orderStatus
        if not o.permId:
            continue  # nouveau ordre pas encore acked par IB
        existing = (await db.execute(
            select(Order).where(Order.ib_perm_id == o.permId)
        )).scalar_one_or_none()
        fields = {
            "ib_order_id": o.orderId,
            "symbol": c.symbol or "",
            "sec_type": c.secType or "",
            "expiry": c.lastTradeDateOrContractMonth or None,
            "strike": Decimal(str(c.strike)) if c.strike else None,
            "right": c.right or None,
            "side": o.action,
            "quantity": Decimal(str(o.totalQuantity)),
            "limit_price": Decimal(str(o.lmtPrice)) if o.lmtPrice else None,
            "status": s.status,
            "filled_qty": Decimal(str(s.filled)),
            "avg_fill_price": Decimal(str(s.avgFillPrice)) if s.avgFillPrice else None,
        }
        if existing is None:
            db.add(Order(ib_perm_id=o.permId, **fields))
        else:
            for k, v in fields.items():
                setattr(existing, k, v)
        upserted += 1
    await db.commit()
    return {"synced": len(trades), "upserted": upserted}


async def sync_trades_from_ib(db: AsyncSession, executor: OrderExecutor) -> dict:
    """Insert one Trade row per IB fill (filled order).

    Schema : 1 row par IB Trade qui a fillé. UNIQUE sur ib_order_id (str).
    On link au position_id via le tuple (symbol, instrument_type, strike,
    maturity, option_type) — best-effort, peut être None si la position a
    déjà été fermée.
    """
    if not executor.is_connected():
        return {"synced": 0, "inserted": 0, "error": "ib_not_connected"}
    ib = executor._ib  # type: ignore[attr-defined]
    trades = ib.trades() if ib else []

    # Index OPEN positions par tuple pour le matching position_id.
    pos_rows = (await db.execute(
        select(Position).where(Position.status == "OPEN")
    )).scalars().all()
    pos_by_key = {_db_position_key(p): p for p in pos_rows}

    inserted = 0
    for t in trades:
        s = t.orderStatus
        if s.status != "Filled" or s.filled <= 0:
            continue
        o = t.order
        c = t.contract
        ib_order_id = str(o.permId or o.orderId)
        existing = (await db.execute(
            select(Trade).where(Trade.ib_order_id == ib_order_id)
        )).scalar_one_or_none()
        if existing is not None:
            continue  # déjà insert

        # Match position par tuple
        key = (
            c.symbol,
            _sec_type_to_instrument_type(c.secType),
            Decimal(str(c.strike)) if c.strike else None,
            _expiry_to_date(c.lastTradeDateOrContractMonth),
            _right_to_option_type(c.right),
        )
        position = pos_by_key.get(key)

        # Timestamp du dernier fill connu, sinon maintenant.
        ts = max((f.time for f in t.fills), default=datetime.now(UTC)) if t.fills else datetime.now(UTC)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        # Commission cumulée
        commission = sum(
            (Decimal(str(f.commissionReport.commission)) for f in t.fills if f.commissionReport),
            Decimal("0"),
        )

        db.add(Trade(
            position_id=position.id if position else None,
            ib_order_id=ib_order_id,
            side=o.action,
            quantity=Decimal(str(s.filled)),
            price=Decimal(str(s.avgFillPrice)) if s.avgFillPrice else Decimal("0"),
            commission=commission if commission else None,
            timestamp=ts,
        ))
        inserted += 1
    await db.commit()
    return {"synced": len(trades), "inserted": inserted}


async def insert_account_snap(db: AsyncSession, executor: OrderExecutor) -> bool:
    """Insert un row dans account_snaps avec NetLiq/Cash/BP/PnL et un dict
    by_currency en JSONB. Cadence plus lente que les positions (l'état du
    compte n'évolue pas en sub-seconde)."""
    if not executor.is_connected():
        return False
    summary = await executor.account_summary()
    if not summary:
        return False

    # Compte les positions OPEN locales (= ce qu'on tracke)
    open_count = (await db.execute(
        select(Position).where(Position.status == "OPEN")
    )).scalars().all()

    snap = AccountSnap(
        timestamp=datetime.now(UTC),
        net_liq_usd=_dec(summary.get("NetLiquidation")),
        cash_usd=_dec(summary.get("TotalCashValue")),
        buying_power_usd=_dec(summary.get("BuyingPower")),
        available_usd=_dec(summary.get("AvailableFunds")),
        unrealized_pnl_usd=_dec(summary.get("UnrealizedPnL")),
        realized_pnl_usd=_dec(summary.get("RealizedPnL")),
        gross_position_value_usd=_dec(summary.get("GrossPositionValue")),
        currencies=summary.get("by_currency") or None,
        open_positions_count=len(open_count),
    )
    db.add(snap)
    await db.commit()
    return True


def _dec(v: float | None) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(round(float(v), 2)))
    except (ValueError, TypeError):
        return None


async def position_sync_loop(
    session_maker: async_sessionmaker[AsyncSession],
    executor: OrderExecutor,
    redis: aioredis.Redis | None = None,
    interval_s: float = SNAPSHOT_INTERVAL_S,
) -> None:
    """Background asyncio task : sync + snapshot toutes les `interval_s` secondes."""
    logger.info("position_sync_loop_started interval=%.1fs", interval_s)

    try:
        async with session_maker() as db:
            sync = await sync_positions_from_ib(db, executor, redis)
            orders = await sync_orders_from_ib(db, executor)
            trades = await sync_trades_from_ib(db, executor)
            logger.info("position_sync_initial sync=%s orders=%s trades=%s", sync, orders, trades)
    except Exception:
        logger.exception("position_sync_initial_failed")

    tick = 0
    while True:
        try:
            await asyncio.sleep(interval_s)
            tick += 1
            async with session_maker() as db:
                sync = await sync_positions_from_ib(db, executor, redis)
                snaps = await insert_snapshots(db, executor, redis)
                orders = await sync_orders_from_ib(db, executor)
                trades = await sync_trades_from_ib(db, executor)
                # Account snap : 1 row toutes les 5 ticks (= ~5s à
                # interval_s=1.0). L'état du compte évolue lentement.
                acct = False
                if tick % 5 == 0:
                    acct = await insert_account_snap(db, executor)
            # Heartbeat → Redis (TTL 300s). Visible dans EngineHealth /
            # /dev/engines comme les 4 autres engines.
            if redis is not None:
                try:
                    ts = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                    await redis.set("heartbeat:execution", ts, ex=300)
                except Exception:
                    logger.exception("heartbeat_write_failed")
            logger.info(
                "position_sync_tick sync=%s snapshots=%s orders=%s trades=%s acct=%s",
                sync, snaps, orders, trades, acct,
            )
        except asyncio.CancelledError:
            logger.info("position_sync_loop_cancelled")
            raise
        except Exception:
            logger.exception("position_sync_tick_failed")

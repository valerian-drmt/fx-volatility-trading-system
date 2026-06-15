"""Sync IB live positions → Postgres `positions` + insert `position_snapshots`.

Pipeline :
  - **sync_positions_from_ib** : fait l'upsert IB → DB. Match par tuple
    (symbol, instrument_type, strike, maturity, option_type) — pas besoin
    d'ajouter une colonne con_id à Position.
  - **publish_portfolio_to_redis** : pour chaque OPEN position, publie sur
    Redis hashes (contract_marks / option_marks / unrealized_pnl) les
    données IB-canoniques. Aucune écriture DB ici — risk-engine est le seul
    writer de ``position_snapshots`` (cf. PORTFOLIO_PANEL_LIVE.md).

Lance au startup api + via un loop périodique (30s).
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from redis import asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from engines.execution.order_executor import OrderExecutor
from persistence.models import (
    AccountSnap,
    Order,
    Position,
    StructureOrder,
    Trade,
    TradePosition,
)
from shared.contracts import multiplier_for, parse_local_symbol

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


def _tenor_bucket(maturity: date | None) -> str | None:
    """Closest FX OTC tenor pillar for ``maturity`` (1W / 2W / 1M / ... / 2Y+).
    Mirrors the same bucketing used by the API's ``_tenor_bucket`` so the
    persisted column matches what the panel computes on the fly.

    Thresholds are midpoints between nominal tenor day counts so a real
    180-day contract (= 6M) lands in the "6M" bucket, not "9M".
    """
    if maturity is None:
        return None
    today = datetime.now(UTC).date()
    days = (maturity - today).days
    if days < 0:
        return "expired"
    if days <= 10:                       # 1W (7) ↔ 2W (14) midpoint ~ 10
        return "1W"
    if days <= 22:                       # 2W (14) ↔ 1M (30)  midpoint 22
        return "2W"
    if days <= 45:                       # 1M (30) ↔ 2M (60)  midpoint 45
        return "1M"
    if days <= 75:                       # 2M (60) ↔ 3M (90)  midpoint 75
        return "2M"
    if days <= 135:                      # 3M (90) ↔ 6M (180) midpoint 135
        return "3M"
    if days <= 225:                      # 6M (180) ↔ 9M (270) midpoint 225
        return "6M"
    if days <= 317:                      # 9M (270) ↔ 1Y (365) midpoint 317
        return "9M"
    if days <= 547:                      # 1Y (365) ↔ 2Y (730) midpoint 547
        return "1Y"
    return "2Y+"


def _ib_position_key(p: dict) -> str | None:
    """Single canonical key = IB ``localSymbol`` (e.g. "6EM6", "EUUN6 C1170")."""
    ls = p.get("local_symbol")
    return ls if ls else None


def _db_position_key(p: Position) -> str | None:
    return p.structure


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
    # Single canonical key — IB ``localSymbol``. We skip rows where IB didn't
    # send a localSymbol (defensive ; should not happen in practice).
    ib_by_key: dict[str, dict] = {}
    for p in ib_active:
        k = _ib_position_key(p)
        if k:
            ib_by_key[k] = p

    db_rows = (await db.execute(
        select(Position)
    )).scalars().all()
    db_by_key: dict[str, Position] = {}
    for p in db_rows:
        k = _db_position_key(p)
        if k:
            db_by_key[k] = p

    now = datetime.now(UTC)
    opened = 0
    unchanged = 0
    for local_sym, ib_pos in ib_by_key.items():
        qty = abs(Decimal(str(ib_pos["position"])))
        side = "BUY" if ib_pos["position"] > 0 else "SELL"
        avg_cost = Decimal(str(ib_pos.get("avg_cost", 0)))
        # Spec resolved from the IB localSymbol — single source of truth for
        # multiplier / strike / option_type. Fallback : raw IB dict fields if
        # the localSymbol doesn't match the known patterns.
        spec = parse_local_symbol(local_sym)
        if spec is not None:
            mult = spec.multiplier
        else:
            mult = multiplier_for(ib_pos.get("symbol"))
        maturity = _expiry_to_date(ib_pos.get("expiry"))
        nominal = qty * Decimal(str(mult))
        cp_entry = (avg_cost / Decimal(str(mult))) if avg_cost else None
        tenor = _tenor_bucket(maturity)
        if local_sym in db_by_key:
            row = db_by_key[local_sym]
            if row.quantity != qty or row.side != side:
                row.quantity = qty
                row.side = side
            else:
                unchanged += 1
            row.expiry = maturity
            row.tenor = tenor
            row.nominal_eur = nominal
            row.contract_price_entry = cp_entry
        else:
            row = Position(
                structure=local_sym,
                side=side,
                tenor=tenor,
                quantity=qty,
                expiry=maturity,
                nominal_eur=nominal,
                contract_price_entry=cp_entry,
                entry_timestamp=now,
            )
            db.add(row)
            opened += 1

    # Closed positions = simply DELETE the row. The audit trail lives in
    # ``trades`` (fills) and ``position_snapshots`` (history).
    closed = 0
    for key, db_row in db_by_key.items():
        if key not in ib_by_key:
            await db.delete(db_row)
            closed += 1

    await db.commit()
    return {"synced": len(ib_active), "opened": opened, "closed": closed, "unchanged": unchanged}


async def publish_portfolio_to_redis(
    db: AsyncSession,
    executor: OrderExecutor,
    redis: aioredis.Redis | None = None,
) -> dict:
    """Read ``ib.portfolio()`` and publish per-contract data on Redis hashes :

      contract_marks:EUR     → {position_id: marketPrice}    (universal)
      option_marks:EUR       → {position_id: marketPrice}    (OPTIONs only, for BS implied vol)
      unrealized_pnl:EUR     → {position_id: unrealizedPNL}  (IB-canonical PnL)

    No DB writes — that's risk-engine's job. The hashes are TTL'd to 600 s so
    a stuck publisher is caught by the API freshness badge.
    """
    if not executor.is_connected() or redis is None:
        return {"published": 0, "error": "ib_not_connected"}
    db_rows = (await db.execute(
        select(Position)
    )).scalars().all()
    if not db_rows:
        return {"published": 0}

    # Key by IB ``localSymbol`` — same canonical id as DB ``positions.structure``.
    portfolio_by_key: dict[str, dict] = {}
    try:
        ib = executor._ib  # type: ignore[attr-defined]
        for p in (ib.portfolio() if ib else []):
            ls = getattr(p.contract, "localSymbol", None)
            if not ls:
                continue
            portfolio_by_key[ls] = {
                "marketPrice": float(p.marketPrice) if p.marketPrice else None,
                "unrealizedPNL": float(p.unrealizedPNL) if p.unrealizedPNL else None,
            }
    except Exception:
        logger.exception("portfolio_lookup_failed")
        return {"published": 0, "error": "portfolio_lookup_failed"}

    contract_marks: dict[str, str] = {}
    option_marks: dict[str, str] = {}
    unrealized_pnl: dict[str, str] = {}
    for db_pos in db_rows:
        pf = portfolio_by_key.get(db_pos.structure or "")
        if not pf:
            continue
        if pf.get("marketPrice") is not None:
            contract_marks[str(db_pos.id)] = str(pf["marketPrice"])
            spec = parse_local_symbol(db_pos.structure)
            if spec is not None and spec.instrument_type == "OPTION":
                option_marks[str(db_pos.id)] = str(pf["marketPrice"])
        if pf.get("unrealizedPNL") is not None:
            unrealized_pnl[str(db_pos.id)] = str(pf["unrealizedPNL"])

    try:
        if contract_marks:
            await redis.hset("contract_marks:EUR", mapping=contract_marks)
            await redis.expire("contract_marks:EUR", 600)
        if option_marks:
            await redis.hset("option_marks:EUR", mapping=option_marks)
            await redis.expire("option_marks:EUR", 600)
        if unrealized_pnl:
            await redis.hset("unrealized_pnl:EUR", mapping=unrealized_pnl)
            await redis.expire("unrealized_pnl:EUR", 600)
    except Exception:
        logger.exception("portfolio_redis_publish_failed")
        return {"published": 0, "error": "redis_publish_failed"}

    return {
        "published": len(contract_marks),
        "options": len(option_marks),
        "pnls": len(unrealized_pnl),
    }


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
    Match sur ``c.localSymbol`` (canonical key, same as ``positions.structure``).
    """
    if not executor.is_connected():
        return {"synced": 0, "inserted": 0, "error": "ib_not_connected"}
    ib = executor._ib  # type: ignore[attr-defined]
    trades = ib.trades() if ib else []

    # Index OPEN positions par localSymbol.
    pos_rows = (await db.execute(
        select(Position)
    )).scalars().all()
    pos_by_key: dict[str, Position] = {p.structure: p for p in pos_rows if p.structure}

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

        position = pos_by_key.get(getattr(c, "localSymbol", "") or "")

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
    """Insert un row dans account_snaps. Pour chaque colonne on essaie
    plusieurs tags IB (alias) — paper et live retournent parfois des
    noms différents. Si aucun ne match, la colonne reste null.
    """
    if not executor.is_connected():
        return False
    summary = await executor.account_summary()
    if not summary:
        return False

    open_count = (await db.execute(
        select(Position)
    )).scalars().all()

    snap = AccountSnap(
        timestamp=datetime.now(UTC),
        net_liq_usd=_pick(summary, ["NetLiquidation", "NetLiquidationByCurrency"]),
        cash_usd=_pick(summary, ["TotalCashValue", "TotalCashBalance", "CashBalance"]),
        unrealized_pnl_usd=_pick(summary, ["UnrealizedPnL"]),
        accrued_cash=_pick(summary, ["AccruedCash"]),
        gross_position_value=_pick(summary, ["GrossPositionValue", "GrossPositionValue-S"]),
        init_margin_req=_pick(summary, ["InitMarginReq", "FullInitMarginReq"]),
        maint_margin_req=_pick(summary, ["MaintMarginReq", "FullMaintMarginReq"]),
        excess_liquidity=_pick(summary, ["ExcessLiquidity", "FullExcessLiquidity"]),
        cushion=_pick(summary, ["Cushion"]),
        currencies=summary.get("by_currency") or {},
        open_positions_count=len(open_count),
    )
    db.add(snap)
    await db.commit()
    return True


def _pick(summary: dict, aliases: list[str]) -> Decimal | None:
    """Try each alias in order, return first non-None mapped to Decimal."""
    for tag in aliases:
        v = summary.get(tag)
        if v is not None:
            try:
                return Decimal(str(round(float(v), 2)))
            except (ValueError, TypeError):
                continue
    return None


def _structure_order_to_ib_key(leg: StructureOrder) -> str | None:
    """Same canonical key as :func:`_ib_position_key` — IB ``localSymbol`` —
    so a leg booked via Step 3 can be matched to the IB-synced position row.

    Reconstructs the localSymbol from the leg's contract attributes ; relies
    on ``shared.contracts`` patterns (6E/M6E for futures, EUU for options).
    """
    if not leg.contract_expiry:
        return None
    try:
        month_letter = "FGHJKMNQUVXZ"[leg.contract_expiry.month - 1]
        year_digit = str(leg.contract_expiry.year)[-1]
    except (AttributeError, IndexError):
        return None
    contract_type = (leg.contract_type or "").lower()
    if contract_type == "future":
        cls = "M6E" if leg.contract_symbol == "M6E" else "6E"
        return f"{cls}{month_letter}{year_digit}"
    if contract_type in ("call", "put") and leg.contract_strike:
        right = "C" if contract_type == "call" else "P"
        strike_code = f"{int(float(leg.contract_strike) * 1000):04d}"
        return f"EUU{month_letter}{year_digit} {right}{strike_code}"
    return None


async def reconcile_trade_positions(
    db: AsyncSession,
    executor: OrderExecutor,
) -> dict:
    """Match each open `trade_position` leg to IB positions and persist
    ``ib_reconciled_at`` / ``ib_qty_total`` / ``ib_qty_diff``.

    Matching is keyed on the contract tuple
    ``(symbol, instrument_type, strike, maturity, option_type)``.

    A booked structure (e.g. straddle = 2 legs) reconciles to the SUM of
    abs(qty) across IB rows that match any of its legs. ``ib_qty_diff``
    is ``booked − ib_total`` (positive ⇒ IB short of expected).

    Skips silently if IB is offline (leaves ``ib_reconciled_at`` untouched
    so the frontend can colour the badge "stale" / "missing").
    """
    if not executor.is_connected():
        return {"reconciled": 0, "error": "ib_not_connected"}

    ib_positions_raw = await executor.list_positions()
    ib_active = [p for p in ib_positions_raw if abs(p.get("position", 0)) > 0]
    ib_qty_by_key: dict[str, int] = {}
    for p in ib_active:
        key = _ib_position_key(p)
        if key is None:
            continue
        ib_qty_by_key[key] = ib_qty_by_key.get(key, 0) + abs(int(p["position"]))

    open_trade_positions = (await db.execute(
        select(TradePosition).where(TradePosition.state == "open")
    )).scalars().all()

    now = datetime.now(UTC)
    reconciled = 0
    for tp in open_trade_positions:
        legs = (await db.execute(
            select(StructureOrder).where(
                StructureOrder.structure_id == tp.structure_id,
                StructureOrder.order_role == "entry",
            )
        )).scalars().all()
        if not legs:
            continue
        booked_qty_total = sum(int(leg.qty_filled or leg.qty or 0) for leg in legs)
        ib_qty_total = 0
        for leg in legs:
            key = _structure_order_to_ib_key(leg)
            if key is not None:
                ib_qty_total += ib_qty_by_key.get(key, 0)
        tp.ib_reconciled_at = now
        tp.ib_qty_total = ib_qty_total
        tp.ib_qty_diff = booked_qty_total - ib_qty_total
        reconciled += 1
        if ib_qty_total == 0 and tp.opened_at < now - timedelta(hours=1):
            logger.warning(
                "trade_position_unreconciled id=%d opened_at=%s booked_qty=%d",
                tp.id, tp.opened_at.isoformat(), booked_qty_total,
            )

    await db.commit()
    return {"reconciled": reconciled}


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
            recon = await reconcile_trade_positions(db, executor)
            logger.info(
                "position_sync_initial sync=%s orders=%s trades=%s recon=%s",
                sync, orders, trades, recon,
            )
    except Exception:
        logger.exception("position_sync_initial_failed")

    while True:
        try:
            await asyncio.sleep(interval_s)
            async with session_maker() as db:
                sync = await sync_positions_from_ib(db, executor, redis)
                snaps = await publish_portfolio_to_redis(db, executor, redis)
                orders = await sync_orders_from_ib(db, executor)
                trades = await sync_trades_from_ib(db, executor)
                recon = await reconcile_trade_positions(db, executor)
                # Account snap : 1 row par tick (cohérent avec les autres
                # tables, alimente account_snaps à 1s).
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
                "position_sync_tick sync=%s snapshots=%s orders=%s trades=%s recon=%s acct=%s",
                sync, snaps, orders, trades, recon, acct,
            )
        except asyncio.CancelledError:
            logger.info("position_sync_loop_cancelled")
            raise
        except Exception:
            logger.exception("position_sync_tick_failed")

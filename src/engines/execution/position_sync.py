"""Sync IB live positions → Postgres `positions` + insert `position_snapshots`.

Pipeline :
  - **sync_positions_from_ib** : fait l'upsert IB → DB. Match par tuple
    (symbol, instrument_type, strike, maturity, option_type) — pas besoin
    d'ajouter une colonne con_id à OpenPosition.
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
import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from redis import asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.products import product_label_from_symbol
from engines.execution.order_executor import OrderExecutor
from persistence.models import (
    AccountHistory,
    BookedPosition,
    BookedPositionMetricHistory,
    OpenPosition,
    StructureOrder,
    TradeEvent,
    TradeStructure,
)
from shared.contracts import build_ib_local_symbol, multiplier_for, parse_local_symbol

# Auto-close booked positions the broker no longer holds (book ↔ IB reconciliation).
# ON by default ; a stale booking that IB shows flat (for >1h) is closed with an
# audit trail. Guarded so it NEVER fires on an empty/disconnected IB snapshot.
_AUTOCLOSE_STALE = os.getenv("RECONCILE_AUTOCLOSE", "1").lower() in ("1", "true", "yes")

logger = logging.getLogger(__name__)

# Fallback cycle when ``position_sync_loop`` is called without an explicit
# ``interval_s`` (tests / scripts). Production runs override this via the
# ``SYNC_INTERVAL_S`` env var read in ``engines.execution.main``.
SNAPSHOT_INTERVAL_S = 5.0

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


def _db_position_key(p: OpenPosition) -> str | None:
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
        select(OpenPosition)
    )).scalars().all()
    db_by_key: dict[str, OpenPosition] = {}
    for p in db_rows:
        k = _db_position_key(p)
        if k:
            db_by_key[k] = p

    # Map IB symbol → (structure_type, trade_id, package_id) — Murex
    # identity stack flowed onto each OpenPosition row. Built once per
    # sync cycle ; structures created after this snapshot get picked up
    # on the next cycle. Symbol candidates absorb the calibrated-strike
    # vs IB-rounded-strike mismatch.
    leg_to_trade = await _build_leg_to_trade_map(db)

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
        # Migration 032 + 034 : prefer the parent structure_type (so a
        # leg of a straddle reads "Straddle") and resolve the trade /
        # package ids ; fall back to symbol-parse + NULL ids for
        # standalone IB-live positions.
        trade_link = leg_to_trade.get(local_sym)
        parent_structure_type = trade_link[0] if trade_link else None
        trade_id = trade_link[1] if trade_link else None
        package_id = trade_link[2] if trade_link else None
        product_label = product_label_from_symbol(local_sym, parent_structure_type)
        con_id = ib_pos.get("con_id")
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
            row.product_label = product_label
            row.contract_id = con_id
            row.trade_id = trade_id
            row.package_id = package_id
        else:
            row = OpenPosition(
                structure=local_sym,
                product_label=product_label,
                contract_id=con_id,
                trade_id=trade_id,
                package_id=package_id,
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

    # Closed positions = DELETE the row (audit trail lives in fills + snapshots).
    # BUT gate the deletes on a TRUSTWORTHY snapshot (T7 — same guard the reaper and
    # reconciler use): a dead/stale IB feed, or a transient empty ``list_positions()``
    # while positions are actually open, must NOT flatten the mirror — that's how a
    # spread leg vanishes minutes after it filled (deleted here, re-added next cycle).
    # Upserts above are always safe (they only add/refresh what IB reported); only the
    # deletes need the guard. A genuinely-flat account (reporting, empty snapshot) still
    # deletes correctly because account_is_reporting() is True.
    trustworthy = executor.account_is_reporting() and not (not ib_active and db_by_key)
    closed = 0
    if trustworthy:
        for key, db_row in db_by_key.items():
            if key not in ib_by_key:
                await db.delete(db_row)
                closed += 1
    elif any(k not in ib_by_key for k in db_by_key):
        logger.warning(
            "position_sync_skip_deletes untrusted_snapshot reporting=%s ib_active=%d db_rows=%d",
            executor.account_is_reporting(), len(ib_active), len(db_by_key),
        )

    await db.commit()
    return {
        "synced": len(ib_active), "opened": opened, "closed": closed,
        "unchanged": unchanged, "deletes_skipped": not trustworthy,
    }


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
        select(OpenPosition)
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


# sync_orders_from_ib + sync_trades_from_ib removed (migration 025 Theme 3):
# they wrote to the legacy `orders` + `trades` tables that have zero readers
# anywhere (no API route, no engine consumer). The canonical state for orders
# lives in `trade_order` (managed by execution-engine fills_handler) and the
# fills journal in `trade_fill`. Removing the parallel cache cleans the
# write path.


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
        select(OpenPosition)
    )).scalars().all()

    snap = AccountHistory(
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


_PARENT_LIVE_STATES = ("submitted", "partial_fill", "fully_filled", "partial_fail")


async def _build_leg_to_trade_map(
    db: AsyncSession,
) -> dict[str, tuple[str, int, int | None]]:
    """Map ``IB localSymbol → (structure_type, trade_id, package_id)``
    for every entry leg of every still-live ``trade_structure``.

    Resolution priority per leg :

      1. ``leg.ib_local_symbol`` (set by fills_handler on first fill).
         Exact match — no rounding, no ambiguity. Always preferred.
      2. Fallback : :func:`_structure_order_to_ib_key` reconstruction
         from the leg's calibrated contract fields. Used only for legs
         that haven't filled yet (and therefore don't have an actual
         IB contract to match against).

    Legs with no resolvable key are silently ignored.

    When the same IB symbol matches multiple still-live structures, the
    most recently created one wins — operationally rare and only happens
    when an exact IB symbol was reused across overlapping trades.
    """
    structures = (await db.execute(
        select(TradeStructure)
        .where(TradeStructure.state.in_(_PARENT_LIVE_STATES))
        .order_by(TradeStructure.created_at)
    )).scalars().all()
    out: dict[str, tuple[str, int, int | None]] = {}
    for ts in structures:
        legs = (await db.execute(
            select(StructureOrder).where(
                StructureOrder.structure_id == ts.id,
                StructureOrder.order_role == "entry",
            )
        )).scalars().all()
        for leg in legs:
            key = leg.ib_local_symbol or _structure_order_to_ib_key(leg)
            if key is not None:
                out[key] = (ts.structure_type, ts.id, ts.package_id)
    return out


def _structure_order_to_ib_key(leg: StructureOrder) -> str | None:
    """Best-guess IB ``localSymbol`` rebuilt from a leg's contract fields.

    Used only as the **fallback** when ``leg.ib_local_symbol`` hasn't been filled
    in yet (pre-fill legs). Delegates to the shared builder (single source of truth
    with the /trade/submitted blotter symbol) — the reconstruction may miss when the
    calibrated strike rounds to a different IB tick than IB picked, but a miss is
    better than the multi-candidate over-claim of the previous implementation.
    """
    return build_ib_local_symbol(
        leg.contract_type, leg.contract_expiry, leg.contract_strike, leg.contract_symbol,
    )


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
        select(BookedPosition).where(BookedPosition.state == "open")
    )).scalars().all()

    now = datetime.now(UTC)
    reconciled = 0
    closed = 0
    # "flat at IB" is only actionable when IB is actively reporting the account
    # (account values streaming). Then an empty position list = genuinely flat →
    # safe to close the stale book. If the feed is dead we never touch it.
    ib_data_live = executor.account_is_reporting()
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
            if _AUTOCLOSE_STALE and ib_data_live:
                # broker holds none of this booking's legs → close it (audited).
                # This is a book-vs-broker adjustment, NOT a trade: leave net_pnl_usd
                # NULL so it never pollutes realized/hit-rate stats. But CAPTURE the
                # last-known mark P&L in the audit event so the P&L that dissolved via
                # netting is attributable — it's what explains the portfolio-stats
                # "reconciliation gap" (Δ net-liq that isn't realized + unrealized).
                last_mark = (await db.execute(
                    select(BookedPositionMetricHistory.current_pnl_net_usd)
                    .where(BookedPositionMetricHistory.position_id == tp.id)
                    .order_by(BookedPositionMetricHistory.timestamp.desc())
                    .limit(1)
                )).scalar_one_or_none()
                tp.state = "closed"
                tp.closed_at = now
                tp.state_updated_at = now
                tp.close_reason = "reconciled_flat_at_ib"
                closed += 1
                db.add(TradeEvent(
                    structure_id=tp.structure_id,
                    event_type="position_reconciled_closed", severity="info",
                    description=(
                        f"booked position {tp.id} flat at IB (booked {booked_qty_total}) "
                        "→ closed by reconciliation"
                    ),
                    payload={
                        "booked_position_id": tp.id,
                        "booked_qty": booked_qty_total,
                        # P&L dissolved via netting (audit only — NOT realized P&L).
                        "dissolved_mark_pnl_usd": (
                            round(float(last_mark), 2) if last_mark is not None else None
                        ),
                    },
                ))
            else:
                logger.warning(
                    "trade_position_unreconciled id=%d opened_at=%s booked_qty=%d",
                    tp.id, tp.opened_at.isoformat(), booked_qty_total,
                )

    await db.commit()
    if closed:
        logger.info("reconcile_closed_stale count=%d", closed)
    return {"reconciled": reconciled, "closed": closed}


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
            recon = await reconcile_trade_positions(db, executor)
            logger.info(
                "position_sync_initial sync=%s recon=%s",
                sync, recon,
            )
    except Exception:
        logger.exception("position_sync_initial_failed")

    from opentelemetry import trace as _otel

    from shared.observability import observed_cycle
    tracer = _otel.get_tracer(__name__)

    while True:
        try:
            await asyncio.sleep(interval_s)
            # P0 obs : each position_sync tick = one cycle. P2 obs : child
            # spans per sub-task so the flame graph shows the slow sub-step.
            with observed_cycle("execution_engine"):
                async with session_maker() as db:
                    with tracer.start_as_current_span("exec_sync_positions"):
                        sync = await sync_positions_from_ib(db, executor, redis)
                    with tracer.start_as_current_span("exec_publish_portfolio_redis"):
                        snaps = await publish_portfolio_to_redis(db, executor, redis)
                    with tracer.start_as_current_span("exec_reconcile"):
                        recon = await reconcile_trade_positions(db, executor)
                    with tracer.start_as_current_span("exec_account_snap"):
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
                "position_sync_tick sync=%s snapshots=%s recon=%s acct=%s",
                sync, snaps, recon, acct,
            )
        except asyncio.CancelledError:
            logger.info("position_sync_loop_cancelled")
            raise
        except Exception:
            logger.exception("position_sync_tick_failed")

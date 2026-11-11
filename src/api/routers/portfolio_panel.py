"""Portfolio panel — account-level aggregate views (cf. PORTFOLIO_PANEL.md).

Bounded context distinct from the legacy `portfolio.py` router (which
served the old Portfolio panel based on the `positions` table, since
removed). Exposed here:

  GET /api/v1/portfolio/account            — latest + prev (≈24h) account_snap
  GET /api/v1/portfolio/equity-curve       — net_liq series, adaptive downsample
  GET /api/v1/portfolio/aggregate-greeks   — Σ Δ Γ V Θ across open positions
  GET /api/v1/portfolio/vega-per-tenor     — vega bucketed by DTE
  GET /api/v1/portfolio/hedge-summary      — multi-window cumul of hedge_orders

P1 + P2 + P3 shipped.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db_session, get_redis
from api.orchestration.greek_caps import compute_live_caps
from bus import keys
from bus.account_cache import read_account_snapshot
from bus.spot_cache import spot_fallbacks
from core.contracts import parse_local_symbol
from core.pricing.bs import bs_delta, bs_gamma, bs_price, bs_theta, bs_vega
from core.risk import greek_limits as gl
from core.risk.attribution import (
    ATTRIB_GROUP_KEYS,
    ATTRIB_SOURCES,
    ATTRIB_SUM_COLS,
    ATTRIB_TENOR_LADDER,
    ATTRIB_TERMS,
    ATTRIB_WING_ORDER,
    assemble_attribution,
    attrib_tenor_bucket,
    attribution_groups,
    decompose_row,
    filter_source,
    iv_fraction_to_pts,
    spot_at_or_before,
)
from core.risk.hist_var import market_shocks, portfolio_pnl, simulate_pnl_by_position
from core.risk.marginal_var import component_var
from core.risk.stress import reval_book
from core.risk.var_factors import factor_var_breakdown
from core.risk.var_stats import MIN_OBS, finite, quantile, var_summary
from core.risk.vega_pca import N_CELLS, PC_NAMES, cell_index, project_vega
from core.units import PIP_SIZE
from persistence.models import (  # noqa: F401
    AccountHistory,
    AppConfigScalar,
    BookedPosition,
    IbConnectionState,
    OpenPosition,
    OpenPositionHistory,
    PcaModel,
    RegimeSnapshot,
    VolSurface,
)
from persistence.rollups import (
    BACKFILL_DAYS,
    compute_attribution,
    latest_account_row,
    read_attribution_latest,
    read_close_before,
    read_nav_series,
    read_portfolio_daily,
    refresh_portfolio_daily,
    underlying_forward,
)
from persistence.rows import account_snapshot_row

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/portfolio", tags=["portfolio-panel"])
DbDep = Annotated[AsyncSession, Depends(get_db_session)]
RedisDep = Annotated[aioredis.Redis, Depends(get_redis)]

# Window → (lookback timedelta, bucket size in seconds for SQL downsampling).
# Constant target ~1k–2k points across the curve.
_WINDOW_SPECS: dict[str, tuple[timedelta, int]] = {
    "1d":  (timedelta(days=1),    60),       # 1 min
    "7d":  (timedelta(days=7),    300),      # 5 min
    "30d": (timedelta(days=30),   1800),     # 30 min
    "1y":  (timedelta(days=365),  14400),    # 4 h
    "all": (timedelta(days=3650), 86400),    # 1 d (EOD)
}

# DTE buckets for /vega-per-tenor.
_TENOR_BUCKETS = [
    ("1M",  0,   30),
    ("2M",  31,  60),
    ("3M",  61,  90),
    ("4M",  91,  120),
    ("6M",  121, 180),
    (">6M", 181, 10_000),
]


def _serialize_snap(s: AccountHistory | None) -> dict[str, Any] | None:
    """Shared with the ``account`` channel through ``persistence.rows`` (#366)."""
    return account_snapshot_row(s)


def _freshness(reference: datetime | None) -> str:
    """Same fresh / stale / missing taxonomy as Step 5 IB sync badge."""
    if reference is None:
        return "missing"
    if reference.tzinfo is None:  # defensive: treat naive timestamps as UTC
        reference = reference.replace(tzinfo=UTC)
    age = datetime.now(UTC) - reference
    if age < timedelta(minutes=5):
        return "fresh"
    if age < timedelta(hours=1):
        return "stale"
    return "missing"


@router.get("/account")
async def get_account(db: DbDep) -> dict[str, Any]:
    """Latest account snapshot + the closest snapshot ≥24h before it.

    Frontend uses ``prev_24h`` to display deltas (Δ Net Liq vs hier, etc.).
    Returns ``latest=None`` if the table is empty (execution-engine never ran).
    """
    latest = (await db.execute(
        select(AccountHistory).order_by(desc(AccountHistory.timestamp)).limit(1)
    )).scalar_one_or_none()

    prev: AccountHistory | None = None
    if latest is not None:
        cutoff = latest.timestamp - timedelta(hours=24)
        prev = (await db.execute(
            select(AccountHistory).where(AccountHistory.timestamp <= cutoff)
            .order_by(desc(AccountHistory.timestamp)).limit(1)
        )).scalar_one_or_none()

    # Buying power / available funds aren't in account_history (the summary snap
    # doesn't keep those IB tags) — read them from the live IB heartbeat row, which
    # the execution-engine refreshes with every connectivity beat.
    hb = (await db.execute(
        select(IbConnectionState).order_by(desc(IbConnectionState.last_heartbeat)).limit(1)
    )).scalar_one_or_none()

    return {
        "latest": _serialize_snap(latest),
        "prev_24h": _serialize_snap(prev),
        "buying_power_usd": float(hb.buying_power_usd) if hb and hb.buying_power_usd is not None else None,
        "available_funds_usd": float(hb.available_funds_usd) if hb and hb.available_funds_usd is not None else None,
        "freshness": _freshness(latest.timestamp if latest else None),
    }


@router.get("/header")
async def header_summary(db: DbDep, redis: RedisDep) -> dict[str, Any]:
    """One-shot endpoint for the dashboard sticky header (panel A).

    Bundles in a single round-trip :
      - the latest account snapshot from the ``account_snapshot`` cache the
        execution engine pushes (#366), falling back to ONE indexed row;
      - the previous UTC day's close from ``portfolio_daily`` for the 24 h
        P&L (same anchor as the Daily P&L panel);
      - aggregate greeks across all OPEN positions (denormalised on
        ``open_position``);
      - 1-day 99% historical VaR on the last 60 daily closes of
        ``portfolio_daily``.
    No scan of ``account_history`` on the request path (#368, closes #286).
    """
    now = datetime.now(UTC)
    latest = await read_account_snapshot(redis) or await latest_account_row(db)
    prev = await read_close_before(db, now)
    # Aggregate greeks — single SUM scan on the denormalised positions row.
    greeks_sql = text("""
        SELECT
          COUNT(*)                            AS n_open,
          COALESCE(SUM(delta_usd), 0)         AS sum_delta,
          COALESCE(SUM(gamma_usd), 0)         AS sum_gamma,
          COALESCE(SUM(vega_usd),  0)         AS sum_vega,
          COALESCE(SUM(theta_usd), 0)         AS sum_theta,
          COALESCE(SUM(current_pnl_usd), 0)   AS sum_pnl
        FROM open_position
    """)
    g = (await db.execute(greeks_sql)).one()
    nav, rollup_at = await read_nav_series(db, days=60)
    daily_nl = [v for _, v in nav]
    var_1d_99: float | None = None
    n_days = max(0, len(daily_nl) - 1)
    if n_days >= 5:
        deltas = sorted(daily_nl[i] - daily_nl[i - 1] for i in range(1, len(daily_nl)))
        var_1d_99 = _percentile(deltas, 0.01)
    nl_now = _coerce_float(latest.get("net_liq_usd")) if latest else None
    nl_prev = float(prev.net_liq_close) if prev is not None and prev.net_liq_close is not None else None
    pnl_24h = (nl_now - nl_prev) if (nl_now is not None and nl_prev is not None) else None
    init_m = _coerce_float(latest.get("init_margin_req")) if latest else None
    util_pct = (init_m / nl_now) if (init_m and nl_now) else None
    return {
        "computed_at": now.isoformat(),
        "rollup_computed_at": rollup_at.isoformat() if rollup_at else None,
        "account": {
            "net_liq_usd": nl_now,
            "cash_usd": _coerce_float(latest.get("cash_usd")) if latest else None,
            "init_margin_req": init_m,
            "excess_liquidity": _coerce_float(latest.get("excess_liquidity")) if latest else None,
            "cushion": _coerce_float(latest.get("cushion")) if latest else None,
            "util_pct": util_pct,
            "n_open_positions": int(g.n_open or 0),
            "as_of": latest.get("timestamp") if latest else None,
        },
        "pnl": {
            "total_24h_usd": pnl_24h,
            "prev_close_day": prev.day.isoformat() if prev is not None else None,
            "open_unrealized_usd": float(g.sum_pnl),
        },
        "greeks": {
            "delta_usd": float(g.sum_delta),
            "gamma_usd": float(g.sum_gamma),
            "vega_usd":  float(g.sum_vega),
            "theta_usd": float(g.sum_theta),
        },
        "var_1d_99": {
            "usd": round(var_1d_99, 2) if var_1d_99 is not None else None,
            "n_days": n_days,
            "method": "historical",
        },
    }


@router.get("/equity-curve")
async def equity_curve(
    db: DbDep,
    window: Literal["1d", "7d", "30d", "1y", "all"] = Query("30d"),
) -> list[dict[str, Any]]:
    """Net liq time series, server-side downsampled to ~1–2k points.

    Implementation : SQL ``DISTINCT ON (bucket)`` keeps the latest snap per
    bucket without ever loading the full row set into Python. EOD = the
    last point of each calendar day (UTC) when its bucketed timestamp
    falls before 22:00 UTC.
    """
    lookback, bucket_secs = _WINDOW_SPECS[window]
    cutoff = datetime.now(UTC) - lookback

    sql = text("""
        SELECT bucket_ts, net_liq_usd
          FROM (
            SELECT DISTINCT ON (bucket_ts)
                   to_timestamp(floor(extract(epoch FROM timestamp) / :bucket) * :bucket)
                       AT TIME ZONE 'UTC' AS bucket_ts,
                   net_liq_usd,
                   timestamp
              FROM account_history
             WHERE timestamp >= :cutoff
             ORDER BY bucket_ts, timestamp DESC
          ) sub
         ORDER BY bucket_ts
    """)
    rs = (await db.execute(sql, {"bucket": bucket_secs, "cutoff": cutoff})).all()
    if not rs:
        return []

    # EOD = last point of each UTC calendar day before 22:00 UTC (FX cash close).
    by_day: dict[str, datetime] = {}
    for ts, _ in rs:
        if ts.hour >= 22:
            continue
        key = ts.strftime("%Y-%m-%d")
        if key not in by_day or ts > by_day[key]:
            by_day[key] = ts
    eod_ts = set(by_day.values())

    return [
        {
            "timestamp": ts.replace(tzinfo=UTC).isoformat(),
            "net_liq_usd": float(nl) if nl is not None else None,
            "is_eod": ts in eod_ts,
        }
        for ts, nl in rs
    ]


def _valuation_series(
    acct: list[tuple[datetime, float | None, dict | None]],
    spots: list[tuple[datetime, float]],
    seed_spot: float | None = None,
) -> list[dict[str, Any]]:
    """Decompose each net-liq snapshot into USD cash + EUR cash ($) + contracts.

    ``acct`` = (bucket_ts, net_liq_usd, currencies JSONB) ascending; ``spots`` =
    (bucket_ts, eurusd) ascending. EUR converts at the latest spot at-or-before
    the snapshot bucket (forward-filled from ``seed_spot``); a ``currencies``
    gap carries the last known breakdown. The contracts part is the RESIDUAL
    net_liq − USD − EUR cash (any other currency implicitly lands there too),
    so the three parts always stack exactly to net liq. Without a usable spot
    or breakdown the parts are None (chart gap) but net liq still plots.
    """
    out: list[dict[str, Any]] = []
    si = 0
    spot = seed_spot
    last_ccy: dict | None = None
    for ts, net_liq, currencies in acct:
        while si < len(spots) and spots[si][0] <= ts:
            spot = spots[si][1]
            si += 1
        if currencies:
            last_ccy = currencies
        usd = eur = contracts = None
        if net_liq is not None and last_ccy is not None and spot is not None:
            usd = _currency_cash(last_ccy.get("USD"))
            eur = _currency_cash(last_ccy.get("EUR")) * spot
            contracts = net_liq - usd - eur
        out.append({
            "timestamp": ts.replace(tzinfo=UTC).isoformat(),
            "net_liq_usd": round(net_liq, 2) if net_liq is not None else None,
            "usd_cash_usd": round(usd, 2) if usd is not None else None,
            "eur_cash_usd": round(eur, 2) if eur is not None else None,
            "contracts_usd": round(contracts, 2) if contracts is not None else None,
        })
    return out


@router.get("/valuation-history")
async def valuation_history(
    db: DbDep,
    window: Literal["1d", "7d", "30d", "1y", "all"] = Query("30d"),
) -> list[dict[str, Any]]:
    """Portfolio-valuation decomposition time series for the Account panel chart.

    Latest ``account_history`` snap per bucket (same downsampling as
    /equity-curve), split by ``_valuation_series`` into USD cash + EUR cash
    (valued at the EURUSD surface spot bucketed the same way) + contracts
    (residual) — three bands footing exactly to the net-liq line.
    """
    lookback, bucket_secs = _WINDOW_SPECS[window]
    cutoff = datetime.now(UTC) - lookback
    acct_sql = text("""
        SELECT bucket_ts, net_liq_usd, currencies
          FROM (
            SELECT DISTINCT ON (bucket_ts)
                   to_timestamp(floor(extract(epoch FROM timestamp) / :bucket) * :bucket)
                       AT TIME ZONE 'UTC' AS bucket_ts,
                   net_liq_usd, currencies, timestamp
              FROM account_history
             WHERE timestamp >= :cutoff
             ORDER BY bucket_ts, timestamp DESC
          ) sub
         ORDER BY bucket_ts
    """)
    spot_sql = text("""
        SELECT bucket_ts, spot
          FROM (
            SELECT DISTINCT ON (bucket_ts)
                   to_timestamp(floor(extract(epoch FROM timestamp) / :bucket) * :bucket)
                       AT TIME ZONE 'UTC' AS bucket_ts,
                   spot, timestamp
              FROM vol_surface_history
             WHERE underlying = 'EURUSD' AND spot IS NOT NULL AND timestamp >= :cutoff
             ORDER BY bucket_ts, timestamp DESC
          ) sub
         ORDER BY bucket_ts
    """)
    seed_sql = text("""
        SELECT spot FROM vol_surface_history
         WHERE underlying = 'EURUSD' AND spot IS NOT NULL AND timestamp < :cutoff
         ORDER BY timestamp DESC LIMIT 1
    """)
    params = {"bucket": bucket_secs, "cutoff": cutoff}
    acct_rows = (await db.execute(acct_sql, params)).all()
    spot_rows = (await db.execute(spot_sql, params)).all()
    seed_row = (await db.execute(seed_sql, {"cutoff": cutoff})).first()
    return _valuation_series(
        [(r.bucket_ts, float(r.net_liq_usd) if r.net_liq_usd is not None else None, r.currencies)
         for r in acct_rows],
        [(r.bucket_ts, float(r.spot)) for r in spot_rows],
        seed_spot=float(seed_row[0]) if seed_row and seed_row[0] is not None else None,
    )


@router.get("/trade-markers")
async def trade_markers(
    db: DbDep,
    days: int = Query(30, ge=1, le=730),
) -> list[dict[str, Any]]:
    """Trade open/close events for the Performance EUR/USD ticker overlay.

    One row per booked position whose open OR close falls within the window. The
    frontend drops a marker at ``opened_at`` (anchored to ``entry_spot``) and, once
    the trade is closed, a second marker at ``closed_at`` — the tooltip carries the
    structure type and realized P&L.
    """
    cutoff = datetime.now(UTC) - timedelta(days=days)
    sql = text("""
        SELECT bp.id,
               COALESCE(ts.structure_type, 'trade') AS stype,
               bp.opened_at, bp.entry_spot, bp.closed_at,
               bp.net_pnl_usd, bp.state
          FROM booked_position bp
          LEFT JOIN trade_structure ts ON bp.structure_id = ts.id
         WHERE bp.opened_at >= :cutoff OR bp.closed_at >= :cutoff
         ORDER BY bp.opened_at
    """)
    rows = (await db.execute(sql, {"cutoff": cutoff})).all()
    return [
        {
            "id": int(r.id),
            "type": str(r.stype),
            "opened_at": r.opened_at.replace(tzinfo=UTC).isoformat() if r.opened_at else None,
            "entry_spot": float(r.entry_spot) if r.entry_spot is not None else None,
            "closed_at": r.closed_at.replace(tzinfo=UTC).isoformat() if r.closed_at else None,
            "net_pnl_usd": float(r.net_pnl_usd) if r.net_pnl_usd is not None else None,
            "state": str(r.state),
        }
        for r in rows
    ]


@router.get("/greeks-history")
async def greeks_history(
    db: DbDep,
    window: Literal["1d", "7d", "30d", "1y", "all"] = Query("30d"),
) -> list[dict[str, Any]]:
    """Portfolio Σ greeks (Δ/Γ/Vega/Θ) time series, server-side downsampled.

    Per time bucket the latest snapshot of each open leg (``open_position_history``,
    written ~every 2s by the risk-engine) is summed → one Σ-greek point per bucket.
    Lets the Performance panel show how each open/close moves the book's greeks.
    """
    lookback, bucket_secs = _WINDOW_SPECS[window]
    cutoff = datetime.now(UTC) - lookback
    sql = text("""
        WITH per_pos AS (
          SELECT DISTINCT ON (bucket_ts, position_id)
                 to_timestamp(floor(extract(epoch FROM timestamp) / :bucket) * :bucket)
                     AT TIME ZONE 'UTC' AS bucket_ts,
                 position_id, delta_usd, gamma_usd, vega_usd, theta_usd
            FROM open_position_history
           WHERE timestamp >= :cutoff
           ORDER BY bucket_ts, position_id, timestamp DESC
        )
        SELECT bucket_ts,
               COALESCE(SUM(delta_usd), 0) AS d,
               COALESCE(SUM(gamma_usd), 0) AS g,
               COALESCE(SUM(vega_usd), 0)  AS v,
               COALESCE(SUM(theta_usd), 0) AS th
          FROM per_pos
         GROUP BY bucket_ts
         ORDER BY bucket_ts
    """)
    rs = (await db.execute(sql, {"bucket": bucket_secs, "cutoff": cutoff})).all()
    return [
        {
            "timestamp": r.bucket_ts.replace(tzinfo=UTC).isoformat(),
            "delta_usd": float(r.d),
            "gamma_usd": float(r.g),
            "vega_usd": float(r.v),
            "theta_usd": float(r.th),
        }
        for r in rs
    ]


def _taylor_pnl_series(
    per_position: dict[int, dict[datetime, dict[str, float | None]]],
    forward: dict[datetime, float | None],
    buckets: list[datetime],
) -> list[dict[str, Any]]:
    """Walk each position bucket-to-bucket and cumulate the four Taylor terms.

    Each interval's increment freezes the greeks at the interval START :
        δ_{t-1}·dS   ½Γ_{t-1}·dS²   V_{t-1}·dσ_pts   Θ_{t-1}·dt_days
    with dS from the shared underlying forward series and dσ from the leg's
    own IV. Increments are summed across positions per bucket, then cumulated
    from the window start so every series begins at 0. A missing input just
    skips that term for that interval (no None poisoning the running sum).
    """
    inc: dict[datetime, list[float]] = {b: [0.0, 0.0, 0.0, 0.0] for b in buckets}
    for snaps in per_position.values():
        for prev_ts, cur_ts in itertools.pairwise(sorted(snaps)):
            prev, cur = snaps[prev_ts], snaps[cur_ts]
            f0, f1 = forward.get(prev_ts), forward.get(cur_ts)
            ds = (f1 - f0) if (f0 is not None and f1 is not None) else None
            iv0, iv1 = prev.get("iv"), cur.get("iv")
            div_pts = (iv1 - iv0) if (iv0 is not None and iv1 is not None) else None
            dt_days = (cur_ts - prev_ts).total_seconds() / 86400.0
            b = inc[cur_ts]
            if prev["delta"] is not None and ds is not None:
                b[0] += prev["delta"] * ds
            if prev["gamma"] is not None and ds is not None:
                b[1] += 0.5 * prev["gamma"] * ds * ds
            if prev["vega"] is not None and div_pts is not None:
                b[2] += prev["vega"] * div_pts
            if prev["theta"] is not None:
                b[3] += prev["theta"] * dt_days
    out: list[dict[str, Any]] = []
    cum = [0.0, 0.0, 0.0, 0.0]
    for b in buckets:
        cum = [c + i for c, i in zip(cum, inc[b], strict=True)]
        out.append({
            "timestamp": b.replace(tzinfo=UTC).isoformat(),
            "delta_pnl_usd": round(cum[0], 2),
            "gamma_pnl_usd": round(cum[1], 2),
            "vega_pnl_usd": round(cum[2], 2),
            "theta_pnl_usd": round(cum[3], 2),
        })
    return out


@router.get("/greek-pnl-history")
async def greek_pnl_history(
    db: DbDep,
    window: Literal["1d", "7d", "30d", "1y", "all"] = Query("30d"),
) -> list[dict[str, Any]]:
    """Cumulative greek-P&L (Taylor terms) time series, server-side downsampled.

    Same decomposition as /pnl-attribution (δ·dS, ½Γ·dS², V·dσ, Θ·dt) but walked
    bucket-by-bucket over the window instead of frozen once over a lookback:
    per open leg, each interval uses the greeks at the interval start, dS from
    the book's underlying forward (the 6E future's market price, forward-filled
    across empty buckets) and dσ from the leg's own IV. Feeds the Performance
    panel's 2×2 greek-P&L grid. IB-live book only (``open_position_history``),
    same source and bucketing as /greeks-history.
    """
    lookback, bucket_secs = _WINDOW_SPECS[window]
    cutoff = datetime.now(UTC) - lookback
    sql = text("""
        WITH per_pos AS (
          SELECT DISTINCT ON (bucket_ts, oph.position_id)
                 to_timestamp(floor(extract(epoch FROM oph.timestamp) / :bucket) * :bucket)
                     AT TIME ZONE 'UTC' AS bucket_ts,
                 oph.position_id,
                 oph.delta_usd, oph.gamma_usd, oph.vega_usd, oph.theta_usd,
                 oph.iv, oph.market_price,
                 (op.structure LIKE '6E%') AS is_future
            FROM open_position_history oph
            JOIN open_position op ON op.id = oph.position_id
           WHERE oph.timestamp >= :cutoff
           ORDER BY bucket_ts, oph.position_id, oph.timestamp DESC
        )
        SELECT * FROM per_pos ORDER BY bucket_ts
    """)
    rs = (await db.execute(sql, {"bucket": bucket_secs, "cutoff": cutoff})).all()
    buckets: list[datetime] = []
    forward: dict[datetime, float | None] = {}
    per_position: dict[int, dict[datetime, dict[str, float | None]]] = {}
    for r in rs:
        ts = r.bucket_ts
        if not buckets or buckets[-1] != ts:
            buckets.append(ts)
        if r.is_future and r.market_price is not None:
            forward[ts] = float(r.market_price)
        per_position.setdefault(int(r.position_id), {})[ts] = {
            "delta": float(r.delta_usd) if r.delta_usd is not None else None,
            # gamma_usd is $/pip; ½Γ·dS² uses a raw-spot dS, so convert to Γ_$
            # ($/spot²) — else the convexity term is 1e4× too small (~$0).
            "gamma": float(r.gamma_usd) / PIP_SIZE if r.gamma_usd is not None else None,
            "vega": float(r.vega_usd) if r.vega_usd is not None else None,
            "theta": float(r.theta_usd) if r.theta_usd is not None else None,
            "iv": float(r.iv) if r.iv is not None else None,
        }
    # Options decompose against the UNDERLYING forward, not their own premium
    # (same rule as /pnl-attribution) — forward-fill buckets with no future snap.
    last: float | None = None
    for ts in buckets:
        if forward.get(ts) is not None:
            last = forward[ts]
        else:
            forward[ts] = last
    return _taylor_pnl_series(per_position, forward, buckets)


@router.get("/aggregate-greeks")
async def aggregate_greeks(db: DbDep) -> dict[str, Any]:
    """Σ Δ Γ V Θ across all OPEN positions (latest snap per position).

    Single SQL pass with ``DISTINCT ON (position_id)`` — no per-position
    sub-query.
    """
    # After migration 028 every row in ``positions`` is OPEN ; live Greeks
    # are denormalised on the row itself (UPDATEd by risk-engine each cycle),
    # so the aggregate is a single GROUP-less scan with no join.
    sql = text("""
        SELECT
          COUNT(*)                              AS n_open,
          COUNT(delta_usd)                      AS n_with_snap,
          COALESCE(SUM(delta_usd), 0)           AS sum_delta,
          COALESCE(SUM(gamma_usd), 0)           AS sum_gamma,
          COALESCE(SUM(vega_usd),  0)           AS sum_vega,
          COALESCE(SUM(theta_usd), 0)           AS sum_theta,
          MAX(timestamp)                        AS last_ts
        FROM open_position
    """)
    row = (await db.execute(sql)).one()
    return {
        "n_open_positions": int(row.n_open or 0),
        "n_with_snapshot": int(row.n_with_snap or 0),
        "total_delta_usd": float(round(row.sum_delta, 2)),
        "total_gamma_usd": float(round(row.sum_gamma, 2)),
        "total_vega_usd": float(round(row.sum_vega, 2)),
        "total_theta_usd": float(round(row.sum_theta, 2)),
        "computed_at": row.last_ts.replace(tzinfo=UTC).isoformat() if row.last_ts else None,
    }


@router.get("/vega-per-tenor")
async def vega_per_tenor(db: DbDep) -> list[dict[str, Any]]:
    """Vega ($/volpt) bucketed by days-to-expiry. Single SQL pass."""
    # Vega lives directly on ``positions.vega_usd`` since migration 028.
    sql = text("""
        SELECT
          GREATEST(0, (expiry - CURRENT_DATE))::int  AS dte,
          vega_usd
        FROM open_position
        WHERE structure LIKE 'EUU%'   -- option contracts on EUR FOP
          AND expiry IS NOT NULL
          AND expiry >= CURRENT_DATE
    """)
    rs = (await db.execute(sql)).all()

    bucket_vega = {b[0]: 0.0 for b in _TENOR_BUCKETS}
    bucket_count = {b[0]: 0 for b in _TENOR_BUCKETS}
    for dte, vega in rs:
        if vega is None:
            continue
        for label, lo, hi in _TENOR_BUCKETS:
            if lo <= dte <= hi:
                bucket_vega[label] += float(vega)
                bucket_count[label] += 1
                break

    return [
        {
            "bucket": label,
            "dte_lo": lo, "dte_hi": hi,
            "vega_usd": round(bucket_vega[label], 2),
            "n_positions": bucket_count[label],
        }
        for label, lo, hi in _TENOR_BUCKETS
    ]


@router.get("/risk-per-tenor")
async def risk_per_tenor(db: DbDep) -> list[dict[str, Any]]:
    """Vega + vanna + volga ($) bucketed by DTE (R11 G-risk). Reads the
    denormalised greek columns on ``open_position`` (no reval needed)."""
    sql = text("""
        SELECT GREATEST(0, (expiry - CURRENT_DATE))::int AS dte,
               vega_usd, vanna_usd, volga_usd
          FROM open_position
         WHERE structure LIKE 'EUU%' AND expiry IS NOT NULL AND expiry >= CURRENT_DATE
    """)
    rs = (await db.execute(sql)).all()
    agg = {b[0]: {"vega": 0.0, "vanna": 0.0, "volga": 0.0, "n": 0} for b in _TENOR_BUCKETS}
    for dte, vega, vanna, volga in rs:
        for label, lo, hi in _TENOR_BUCKETS:
            if lo <= dte <= hi:
                a = agg[label]
                a["vega"] += float(vega or 0.0)
                a["vanna"] += float(vanna or 0.0)
                a["volga"] += float(volga or 0.0)
                a["n"] += 1
                break
    return [
        {
            "bucket": label, "dte_lo": lo, "dte_hi": hi,
            "vega_usd": round(agg[label]["vega"], 2),
            "vanna_usd": round(agg[label]["vanna"], 2),
            "volga_usd": round(agg[label]["volga"], 2),
            "n_positions": int(agg[label]["n"]),
        }
        for label, lo, hi in _TENOR_BUCKETS
    ]


@router.get("/hedge-summary")
async def hedge_summary(db: DbDep) -> dict[str, Any]:
    """Multi-window cumul of `hedge_orders`. Pattern Risk Ops standard :
    a drift surfaces by comparing several windows side-by-side (today
    sharp vs 7d normal → local event ; 30d up vs today calm → structural).

    Only counts FILLED hedges (state='filled'). All windows are anchored
    to ``now`` UTC ; calendar windows (today/WTD/MTD/YTD) use UTC midnight
    or the latest UTC Mon/01-of-month/Jan-1 boundary.
    """
    sql = text("""
        SELECT
          COUNT(*) FILTER (WHERE triggered_at >= :today_start)        AS n_today,
          COALESCE(SUM(total_cost_usd) FILTER (WHERE triggered_at >= :today_start), 0)  AS cost_today,
          COUNT(*) FILTER (WHERE triggered_at >= :wtd_start)          AS n_wtd,
          COALESCE(SUM(total_cost_usd) FILTER (WHERE triggered_at >= :wtd_start), 0)    AS cost_wtd,
          COUNT(*) FILTER (WHERE triggered_at >= :mtd_start)          AS n_mtd,
          COALESCE(SUM(total_cost_usd) FILTER (WHERE triggered_at >= :mtd_start), 0)    AS cost_mtd,
          COUNT(*) FILTER (WHERE triggered_at >= :ytd_start)          AS n_ytd,
          COALESCE(SUM(total_cost_usd) FILTER (WHERE triggered_at >= :ytd_start), 0)    AS cost_ytd,
          COUNT(*) FILTER (WHERE triggered_at >= :r7d_start)          AS n_r7d,
          COALESCE(SUM(total_cost_usd) FILTER (WHERE triggered_at >= :r7d_start), 0)    AS cost_r7d,
          COUNT(*) FILTER (WHERE triggered_at >= :r30d_start)         AS n_r30d,
          COALESCE(SUM(total_cost_usd) FILTER (WHERE triggered_at >= :r30d_start), 0)   AS cost_r30d
        FROM hedge_order
        WHERE state = 'filled'
    """)
    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # ISO weekday : Mon = 0 here (since weekday()).
    wtd_start  = today_start - timedelta(days=now.weekday())
    mtd_start  = today_start.replace(day=1)
    ytd_start  = today_start.replace(month=1, day=1)
    r7d_start  = now - timedelta(days=7)
    r30d_start = now - timedelta(days=30)

    row = (await db.execute(sql, {
        "today_start": today_start, "wtd_start":  wtd_start,
        "mtd_start":   mtd_start,   "ytd_start":  ytd_start,
        "r7d_start":   r7d_start,   "r30d_start": r30d_start,
    })).one()

    def _bucket(n: int, c: float) -> dict[str, float | int]:
        return {"n_hedges": int(n or 0), "cum_cost_usd": float(round(c or 0.0, 2))}

    return {
        "today":       _bucket(row.n_today, row.cost_today),
        "wtd":         _bucket(row.n_wtd,   row.cost_wtd),
        "mtd":         _bucket(row.n_mtd,   row.cost_mtd),
        "ytd":         _bucket(row.n_ytd,   row.cost_ytd),
        "rolling_7d":  _bucket(row.n_r7d,   row.cost_r7d),
        "rolling_30d": _bucket(row.n_r30d,  row.cost_r30d),
        "computed_at": now.isoformat(),
    }


# Shock bins. Spot = bp move ; vol/skew/fly = vol points ; time = days decayed.
_STRESS_SPOT_BPS = [-200, -100, -50, 0, 50, 100, 200]
_STRESS_VOL_VPS = [3, 1, 0, -1, -3]      # rows top→bottom (+3 vp on top)
_STRESS_TIME_DAYS = [40, 20, 10, 5, 0]   # rows top→bottom (most decay on top)
_STRESS_SKEW_VPS = [2, 1, 0, -1, -2]     # ΔRR vol points
_STRESS_FLY_VPS = [2, 1, 0, -1, -2]      # ΔBF vol points
_LADDER_SPOT_BPS = [-400, -200, 0, 200, 400]
_LADDER_VOL_VPS = [-3, -1, 0, 1, 3]
_LADDER_TIME_DAYS = [0, 5, 10, 20, 40]
_LADDER_SKEW_VPS = [-2, -1, 0, 1, 2]
_LADDER_FLY_VPS = [-2, -1, 0, 1, 2]

# Row-axis → (bins, kwarg-name for reval_book, unit label).
_STRESS_ROW_AXES: dict[str, tuple[list[int], str, str]] = {
    "spot-vol":  (_STRESS_VOL_VPS,   "dvol_vp",  "vp"),
    "spot-time": (_STRESS_TIME_DAYS, "dt_days",  "d"),
    "spot-skew": (_STRESS_SKEW_VPS,  "dskew_vp", "vp"),
    "spot-fly":  (_STRESS_FLY_VPS,   "dfly_vp",  "vp"),
}
_LADDER_AXES: dict[str, tuple[list[int], str, str]] = {
    "spot": (_LADDER_SPOT_BPS, "dspot_bp", "bp"),
    "vol":  (_LADDER_VOL_VPS,  "dvol_vp",  "vp"),
    "time": (_LADDER_TIME_DAYS, "dt_days", "d"),
    "skew": (_LADDER_SKEW_VPS, "dskew_vp", "vp"),
    "fly":  (_LADDER_FLY_VPS,  "dfly_vp",  "vp"),
}
_OUTPUTS = ("pnl", "delta", "gamma", "vega", "theta", "vanna", "volga")


@dataclass(frozen=True)
class ResolvedBook:
    """What the stress / VaR endpoints price, and what they could not (#316,
    audit 03#5): every open row is either in ``baselines`` or in ``dropped``
    with a reason, and ``fallback`` names any proxy the numbers rest on."""

    spot: float | None
    baselines: list[dict[str, Any]]
    spot_source: str | None
    dropped: list[dict[str, Any]]
    fallback: dict[str, str]

    @property
    def n_open(self) -> int:
        return len(self.baselines) + len(self.dropped)


async def _surface_forward(redis: aioredis.Redis | None, symbol: str = "EURUSD") -> float | None:
    """The level the vol engine calibrated its latest surface on, refreshed
    every vol cycle (#432). `publish_vol_update` writes it as `spot` (the
    calibration forward); `forward` is read first for payloads that carry it."""
    if redis is None:
        return None
    try:
        raw = await redis.get(keys.LATEST_VOL_SURFACE.format(symbol=symbol))
        payload = json.loads(raw) if raw else {}
        forward = payload.get("forward", payload.get("spot"))
        value = float(forward) if forward is not None else None
    except Exception:  # a cache miss or a bad payload is "no forward", never a 500
        return None
    return value if value is not None and math.isfinite(value) and value > 0 else None


async def _book_spot(open_positions: Sequence[Any], redis: aioredis.Redis | None) -> tuple[float | None, str | None]:
    """The underlying level the book is revalued around, best source first:
    a held 6E future's IB mark; else the vol surface's forward (paper IB often
    marks nothing, #432); else an option strike (an ATM-ish guess, tens of pips
    off). Returns ``(spot, source)``; ``(None, None)`` when none applies."""
    for p in open_positions:
        spec = parse_local_symbol(p.structure)
        if spec and spec.instrument_type == "FUTURE" and p.market_price:
            return float(p.market_price), "future_mark"
    forward = await _surface_forward(redis)
    if forward is not None:
        return forward, "surface_forward"
    for p in open_positions:
        spec = parse_local_symbol(p.structure)
        if spec and spec.option_type and spec.strike:
            logger.warning("stress_spot_strike_proxy position_id=%s strike=%s", p.id, spec.strike)
            return float(spec.strike), "strike_proxy"
    return None, None


async def _resolve_book(db: AsyncSession, redis: aioredis.Redis | None = None) -> ResolvedBook:
    """Resolve OPEN positions -> ``ResolvedBook`` for ``reval_book``.

    Spot proxy : any FUTURE market_price (``spot_source="future_mark"``), else
    an option strike (``"strike_proxy"``: an ATM-ish guess that can be tens of
    pips off; every stress / ladder number built on it is approximate, and the
    response says so in ``fallback`` instead of hiding it — audit 03#5, #310).

    Each option baseline stores the per-unit BS ``price_base`` at current iv.
    An option with no iv, no expiry or an unparseable symbol is not silently
    skipped: it lands in ``dropped`` with the reason, and ``n_positions``
    counts only what was priced.
    """
    open_positions = (await db.execute(select(OpenPosition))).scalars().all()
    if not open_positions:
        return ResolvedBook(None, [], None, [], {})

    current_spot, spot_source = await _book_spot(open_positions, redis)
    # Anything but the held future's own mark is a proxy, and the payload says so.
    fallback: dict[str, str] = {"spot": spot_source} if spot_source in ("surface_forward", "strike_proxy") else {}
    if current_spot is None:
        dropped_all = [{"id": str(p.id), "reason": "no_spot"} for p in open_positions]
        return ResolvedBook(None, [], None, dropped_all, fallback)

    today = datetime.now(UTC).date()
    baselines: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for p in open_positions:
        spec = parse_local_symbol(p.structure)
        if spec is None:
            dropped.append({"id": str(p.id), "reason": "unparseable_symbol"})
            continue
        qty_signed = float(p.quantity) * (1.0 if p.side == "BUY" else -1.0)
        if spec.instrument_type == "FUTURE":
            baselines.append({
                "id": str(p.id), "type": "FUTURE",
                "qty_signed": qty_signed, "mult": spec.multiplier,
            })
        elif spec.option_type and spec.strike and p.expiry and p.iv:
            T = max(0.001, (p.expiry - today).days / 365.0)
            iv_dec = float(p.iv)
            right = "C" if spec.option_type == "CALL" else "P"
            baselines.append({
                "id": str(p.id), "type": "OPTION",
                "qty_signed": qty_signed, "mult": spec.multiplier,
                "K": float(spec.strike), "T": T, "iv": iv_dec, "right": right,
                "price_base": bs_price(current_spot, float(spec.strike), T, iv_dec, right),
            })
        else:
            reason = (
                "no_iv" if not p.iv
                else "no_expiry" if not p.expiry
                else "unsupported_contract"
            )
            dropped.append({"id": str(p.id), "reason": reason})
            logger.warning("stress_position_dropped position_id=%s reason=%s", p.id, reason)
    return ResolvedBook(current_spot, baselines, spot_source, dropped, fallback)


@router.get("/stress-grid")
async def stress_grid(
    db: DbDep,
    redis: RedisDep,
    axis: str = Query("spot-vol", pattern="^spot-(vol|time|skew|fly)$"),
    output: str = Query("pnl"),
) -> dict[str, Any]:
    """Parameterised spot × {vol|time|skew|fly} stress matrix (R11 G-risk 5.2).

    Columns = spot bp bins ; rows = the chosen 2nd axis. Each cell = the chosen
    ``output`` (pnl = ΔNPV vs now ; any greek = the book greek at that scenario),
    full-BS revalued via ``core.risk.stress.reval_book``. ``axis=spot-vol,
    output=pnl`` reproduces the legacy 5×7 grid (``vol_bins_vps`` kept for compat).
    """
    if output not in _OUTPUTS:
        raise HTTPException(422, f"output must be one of {_OUTPUTS}")
    row_bins, row_kw, row_unit = _STRESS_ROW_AXES[axis]
    book = await _resolve_book(db, redis)
    current_spot, baselines, spot_source = book.spot, book.baselines, book.spot_source
    base = {
        "current_spot": round(current_spot, 5) if current_spot else None,
        "spot_source": spot_source, "dropped": book.dropped, "fallback": book.fallback,
        "axis": axis, "output": output,
        "spot_bins_bps": _STRESS_SPOT_BPS, "row_bins": row_bins, "row_unit": row_unit,
        "n_positions": len(baselines),
    }
    if axis == "spot-vol":
        base["vol_bins_vps"] = _STRESS_VOL_VPS  # legacy field
    if current_spot is None:
        return {**base, "grid": []}

    grid: list[list[float]] = []
    for row_val in row_bins:
        row: list[float] = []
        for dspot_bp in _STRESS_SPOT_BPS:
            v = reval_book(
                baselines, current_spot,
                dspot_bp=dspot_bp, output=output, **{row_kw: float(row_val)},
            )
            row.append(round(v, 2))
        grid.append(row)
    return {**base, "grid": grid}


@router.get("/greeks-ladder")
async def greeks_ladder(
    db: DbDep,
    redis: RedisDep,
    axis: str = Query("spot", pattern="^(spot|vol|time|skew|fly)$"),
) -> dict[str, Any]:
    """Per-bin greeks ladder along one axis (R11 G-risk 5.3). Each row = P&L +
    Δ/Γ/Vega revalued at that shock. ``hedge_delta_usd = −delta_usd``. The
    ``axis`` column name (``dspot_bps``/``dvol_vps``/…) reflects the chosen axis."""
    bins, kw, unit = _LADDER_AXES[axis]
    book = await _resolve_book(db, redis)
    current_spot, baselines, spot_source = book.spot, book.baselines, book.spot_source
    base = {
        "current_spot": round(current_spot, 5) if current_spot else None,
        "spot_source": spot_source, "dropped": book.dropped, "fallback": book.fallback,
        "axis": axis, "bins": bins, "unit": unit, "n_positions": len(baselines),
    }
    if axis == "spot":
        base["spot_bins_bps"] = _LADDER_SPOT_BPS  # legacy field
    if current_spot is None:
        return {**base, "rows": []}

    rows: list[dict[str, Any]] = []
    for b in bins:
        kwargs = {kw: float(b)}
        # reval_book's delta is EUR-equivalent exposure; × spot = USD (#313).
        delta = reval_book(baselines, current_spot, output="delta", **kwargs) * current_spot
        row = {
            "axis_value": b,
            "pnl_usd": round(reval_book(baselines, current_spot, output="pnl", **kwargs), 2),
            "delta_usd": round(delta, 2),
            "gamma_usd_per_pip": round(reval_book(baselines, current_spot, output="gamma", **kwargs), 2),
            "vega_usd_per_volpt": round(reval_book(baselines, current_spot, output="vega", **kwargs), 2),
            "hedge_delta_usd": -round(delta, 2),
        }
        if axis == "spot":
            row["dspot_bps"] = b
            row["spot"] = round(current_spot * (1.0 + b / 10000.0), 5)
        rows.append(row)
    return {**base, "rows": rows}


@router.get("/vega-pca")
async def vega_pca(db: DbDep, redis: RedisDep) -> dict[str, Any]:
    """Project the book's per-cell vega onto the active PCA loadings (R11 G-risk).

    Each open option is classified into a 30-dim grid cell (DTE -> tenor, BS delta
    -> delta bucket) and its vega ($/vol-pt) accumulated. The active model's
    loadings + stds then give the book's vega P&L sensitivity to each PC
    (level / slope / curvature) -- see ``core.risk.vega_pca``.
    """
    book = await _resolve_book(db, redis)
    current_spot, baselines, spot_source = book.spot, book.baselines, book.spot_source
    opts = [b for b in baselines if b["type"] == "OPTION"]
    vega_cells = [0.0] * N_CELLS
    if current_spot is not None:
        for b in opts:
            d = bs_delta(current_spot, b["K"], b["T"], b["iv"], b["right"])
            vega = bs_vega(current_spot, b["K"], b["T"], b["iv"]) * b["qty_signed"] * b["mult"] * 0.01
            vega_cells[cell_index(b["T"] * 365.0, d)] += vega
    model = (
        await db.execute(select(PcaModel).where(PcaModel.is_active.is_(True)).limit(1))
    ).scalar_one_or_none()
    base = {
        "current_spot": round(current_spot, 5) if current_spot else None,
        "spot_source": spot_source, "dropped": book.dropped, "fallback": book.fallback,
        "n_positions": len(opts),
    }
    if model is None:
        return {**base, "model_version": None, "pcs": []}
    proj = project_vega(vega_cells, model.loadings, model.stds)
    var_ratio = model.variance_explained_ratio or []
    pcs = [
        {
            "pc": i + 1,
            "name": PC_NAMES.get(i + 1, f"pc{i + 1}"),
            "variance_pct": round(float(var_ratio[i]) * 100, 1) if i < len(var_ratio) else None,
            "vega_usd": round(proj[i], 2),
        }
        for i in range(len(proj))
    ]
    return {**base, "model_version": model.version, "pcs": pcs}


@router.get("/marginal-var")
async def marginal_var(db: DbDep, redis: RedisDep) -> dict[str, Any]:
    """Per-position component VaR over the open book (R11 G-risk).

    Decomposes the 99% portfolio VaR into per-position standalone + component
    contributions (Euler allocation, see ``core.risk.marginal_var``). The factor
    tag is the position's dominant greek (spot / level / skew / curv).

    Same two methods as ``/var``, and for the same reason: each position's P&L
    series is preferably its own vector across the ~1y of replayed market
    scenarios (``historical-simulation``, available immediately), falling back to
    its realised daily P&L deltas out of ``open_position_history``
    (``account-history``, needs ~5 days to accumulate).
    """
    positions = (await db.execute(select(OpenPosition))).scalars().all()
    if not positions:
        return {"positions": [], "total": None, "n_days": 0, "method": "historical-simulation",
                "convention": "loss", "horizon_days": 1, "dropped": [], "fallback": {}}
    meta: dict[str, dict[str, Any]] = {}
    for pos in positions:
        cand = {
            "spot": abs(float(pos.delta_usd or 0)),
            "level": abs(float(pos.vega_usd or 0)),
            "skew": abs(float(pos.vanna_usd or 0)),
            "curv": abs(float(pos.volga_usd or 0)),
        }
        meta[str(pos.id)] = {
            "label": pos.product_label or pos.structure,
            "trade": (
                f"T-{pos.trade_id}" if pos.trade_id is not None
                else f"PKG-{pos.package_id}" if pos.package_id is not None
                else None
            ),
            "factor": max(cand, key=lambda k: cand[k]),
        }
    sim = await _hist_sim(db, redis)
    if sim is not None:
        series_by_id: dict[str, list[float]] = sim["by_id"]
        method = "historical-simulation"
    else:
        rows = (await db.execute(text("""
            WITH daily AS (
              SELECT DISTINCT ON (position_id, date_trunc('day', timestamp))
                     position_id, date_trunc('day', timestamp) AS day, current_pnl_usd
                FROM open_position_history
               WHERE timestamp >= NOW() - INTERVAL '120 days' AND current_pnl_usd IS NOT NULL
               ORDER BY position_id, date_trunc('day', timestamp), timestamp DESC
            )
            SELECT position_id, current_pnl_usd FROM daily ORDER BY position_id, day
        """))).all()
        cum: dict[str, list[float]] = {}
        for pid, pnl in rows:
            cum.setdefault(str(pid), []).append(float(pnl))
        series_by_id = {
            pid: [vals[i] - vals[i - 1] for i in range(1, len(vals))]
            for pid, vals in cum.items()
            if len(vals) >= 2
        }
        method = "account-history"
    res = component_var(series_by_id)
    out = [
        {**row, "label": meta.get(row["id"], {}).get("label", row["id"]),
         "trade": meta.get(row["id"], {}).get("trade"),
         "factor": meta.get(row["id"], {}).get("factor", "spot")}
        for row in res["positions"]
    ]
    total = (
        {"portfolio_var_usd": res["portfolio_var_usd"], "diversification_pct": res["diversification_pct"]}
        if out else None
    )
    dropped = list(sim["meta"]["dropped"]) if sim is not None else []
    dropped += res.get("dropped", [])
    return {
        "positions": out, "total": total, "n_days": res["n_days"], "method": method,
        # Positive USD losses here; /var serves P&L quantiles (negative = loss).
        # One helper decides both: core.risk.var_stats.
        "convention": "loss", "horizon_days": 1,
        "dropped": dropped,
        "fallback": dict(sim["meta"]["fallback"]) if sim is not None else {},
    }


@router.get("/var-factors")
async def var_factors(db: DbDep, redis: RedisDep) -> dict[str, Any]:
    """Scenario VaR by factor (spot / level / skew / curv) over the open book (R11 G-risk).

    Each factor's VaR is the book's loss under its 1-day 99% adverse move, full-BS
    revalued via ``core.risk.var_factors`` — derived entirely from the live book
    (the shock sizes are documented desk assumptions). Empty when the book/spot is missing.
    """
    book = await _resolve_book(db, redis)
    spot, baselines, spot_source = book.spot, book.baselines, book.spot_source
    if spot is None or not baselines:
        return {"current_spot": round(spot, 5) if spot else None, "n_positions": 0, "factors": []}
    return {
        "current_spot": round(spot, 5),
        "spot_source": spot_source, "dropped": book.dropped, "fallback": book.fallback,
        "n_positions": len(baselines),
        "factors": factor_var_breakdown(baselines, spot),
    }


# ──────────────────────────────────────────────────────────────────────
# Panel G — P&L attribution over a lookback window
# ──────────────────────────────────────────────────────────────────────

# group_by → the per-position field it buckets on.
# The attribution maths lives in ``core.risk.attribution`` and the queries in
# ``persistence.rollups`` (#368): the analytics engine computes the rows, this
# router shapes them. The underscored names stay as aliases for the tests and
# for anyone grepping the old call sites.
_ATTRIB_GROUP_KEYS = ATTRIB_GROUP_KEYS
_ATTRIB_TERMS = ATTRIB_TERMS
_ATTRIB_SUM_COLS = ATTRIB_SUM_COLS
_ATTRIB_TENOR_LADDER = ATTRIB_TENOR_LADDER
_ATTRIB_WING_ORDER = ATTRIB_WING_ORDER
_ATTRIB_SOURCES = ATTRIB_SOURCES
_attrib_tenor_bucket = attrib_tenor_bucket
_filter_source = filter_source
_iv_fraction_to_pts = iv_fraction_to_pts
_attribution_groups = attribution_groups
_decompose_row = decompose_row
_underlying_forward = underlying_forward
_spot_fallbacks = spot_fallbacks
spot_at_or_before = spot_at_or_before  # re-exported for the attribution tests


@router.get("/pnl-attribution")
async def pnl_attribution(
    db: DbDep,
    redis: RedisDep,
    lookback_hours: int = Query(24, ge=1, le=168),  # 1h..7d
    group_by: str | None = Query(None),  # None → totals+per_position ; tenor → matrix
    # Which legs feed the totals / groups. ib_live = the IB mirror the desk
    # trades on (default, what every panel shows); booked = the legacy
    # mock-mode book; all = both. Totals and groups are computed on the same
    # filtered set, so the by-tenor and by-trade panels foot (#377).
    source: str = Query("ib_live", pattern="^(ib_live|booked|all)$"),
) -> dict[str, Any]:
    """Decompose realized P&L into greek contributions over the window.

    Per-position Taylor expansion anchored on the window-START (t-1) greeks
    (``core.risk.attribution.decompose_row``). The analytics engine computes
    it every 5 min for the standard lookbacks and stores one row per window
    (``pnl_attribution_latest``, #368); this endpoint reads that row and
    filters / pivots it. It computes inline only when there is no fresh row
    (engine not running, or a lookback it does not keep warm), so a panel
    never blanks on a cold stack. ``computed_at`` is the rollup's time;
    ``rollup`` says which path answered.
    """
    if group_by is not None and group_by not in ATTRIB_GROUP_KEYS:
        raise HTTPException(400, f"unknown group_by '{group_by}' (expected tenor / structure / wing)")
    now = datetime.now(UTC)
    stored = await read_attribution_latest(db, lookback_hours=lookback_hours, now=now)
    if stored is None:
        fallback = await spot_fallbacks(redis, now - timedelta(hours=lookback_hours))
        stored = await compute_attribution(db, now=now, lookback_hours=lookback_hours, spot_fallback=fallback)
        stored["meta"]["rollup"] = "inline"
    else:
        stored["meta"]["rollup"] = "materialised"
    return assemble_attribution(stored["per_position"], stored["meta"], source=source, group_by=group_by)


# ──────────────────────────────────────────────────────────────────────
# Panel J — Pin risk grid (full BS revaluation at strike ± breach)
# ──────────────────────────────────────────────────────────────────────


_BREACH_BPS = 50  # ±50 bp around the strike


@router.get("/pin-risk")
async def pin_risk(db: DbDep, redis: RedisDep) -> dict[str, Any]:
    """Full BS revaluation per option at strike (pin) and strike ± 50 bp.

    The frontend's old linearised approximation (Δ × ΔS) was a poor
    proxy near expiry where Γ dominates. Here we do the proper reval:

      pnl_at_pin       = NPV(spot=K) - NPV(spot=now)
      pnl_at_breach_up = NPV(spot=K + 50bp) - NPV(spot=now)
      pnl_at_breach_dn = NPV(spot=K - 50bp) - NPV(spot=now)

    All computed at the position's current T and IV (no time decay, no
    vol shock — operator can run those via the stress-grid panel).
    Futures are ignored (no pin risk — payoff is linear in spot).
    """
    open_positions = (await db.execute(select(OpenPosition))).scalars().all()
    if not open_positions:
        return {"current_spot": None, "rows": [], "n_options": 0}

    # Same underlying level as stress-grid (#432).
    current_spot, _source = await _book_spot(open_positions, redis)
    if current_spot is None:
        return {"current_spot": None, "rows": [], "n_options": 0}

    today = datetime.now(UTC).date()
    rows: list[dict[str, Any]] = []
    for p in open_positions:
        spec = parse_local_symbol(p.structure)
        if spec is None or not spec.option_type or not spec.strike:
            continue  # skip futures + malformed
        if not p.expiry or not p.iv:
            continue  # need T + IV for BS reval
        dte = (p.expiry - today).days
        T = max(0.001, dte / 365.0)
        iv_dec = float(p.iv)
        right = "C" if spec.option_type == "CALL" else "P"
        K = float(spec.strike)
        qty_signed = float(p.quantity) * (1.0 if p.side == "BUY" else -1.0)
        mult = spec.multiplier
        notional = qty_signed * mult
        breach_step = K * _BREACH_BPS / 10_000.0  # 50 bp of strike in spot units

        try:
            npv_now = bs_price(current_spot, K, T, iv_dec, right) * notional
            npv_pin = bs_price(K, K, T, iv_dec, right) * notional
            npv_up  = bs_price(K + breach_step, K, T, iv_dec, right) * notional
            npv_dn  = bs_price(K - breach_step, K, T, iv_dec, right) * notional
        except Exception:
            continue  # bad BS input — skip rather than 500

        rows.append({
            "id": int(p.id),
            # Booking linkage — lets the frontend group legs by trade like the
            # Position breakdown table (NULL for direct-IB positions).
            "trade_id": int(p.trade_id) if p.trade_id is not None else None,
            "structure": p.structure,
            "product_label": p.product_label,
            "side": p.side,
            "option_type": spec.option_type,
            "strike": K,
            "expiry": p.expiry.isoformat(),
            "dte_days": dte,
            "qty": float(p.quantity),
            "distance_pips": round((current_spot - K) * 10_000, 1),
            "pnl_now_usd": float(p.current_pnl_usd) if p.current_pnl_usd is not None else None,
            "delta_usd": float(p.delta_usd) if p.delta_usd is not None else None,
            "pnl_at_pin_usd": round(npv_pin - npv_now, 2),
            "pnl_at_breach_up_usd": round(npv_up - npv_now, 2),
            "pnl_at_breach_dn_usd": round(npv_dn - npv_now, 2),
        })
    rows.sort(key=lambda r: r["dte_days"])  # most-urgent first
    return {
        "current_spot": round(current_spot, 5),
        "breach_bps": _BREACH_BPS,
        "rows": rows,
        "n_options": len(rows),
    }


# ──────────────────────────────────────────────────────────────────────
# Panel — Scenarios (5 charts : PnL vs spot + Δ/Γ/Vega/Θ vs spot or vol)
# ──────────────────────────────────────────────────────────────────────


_SCENARIO_SPOT_STEPS_PCT: list[float] = [
    -5.0, -4.0, -3.0, -2.0, -1.5, -1.0, -0.5, -0.25,
    0.0, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0,
]
_SCENARIO_IV_STEPS_VOLPT: list[float] = [
    -5.0, -4.0, -3.0, -2.0, -1.0, -0.5,
    0.0, 0.5, 1.0, 2.0, 3.0, 4.0, 5.0,
]


@router.get("/scenarios")
async def scenarios(db: DbDep) -> dict[str, Any]:
    """Two-axis full-reval scenario surface for the live book.

    Axis 1 (spot shocks, fixed IV) : revalue every position at
    ``spot × (1 + step/100)``. Returns one row per spot step with PnL +
    4 net greeks. Used by the 5-chart Portfolio scenarios panel.

    Axis 2 (IV shocks, fixed spot) : shift each option's IV by ``step``
    vol-points, recompute price + greeks. Futures contribute 0 (no IV
    exposure).
    """
    open_positions = (await db.execute(select(OpenPosition))).scalars().all()
    base_payload: dict[str, Any] = {
        "current_spot": None, "current_iv_avg_pct": None,
        "spot_steps_pct": _SCENARIO_SPOT_STEPS_PCT,
        "iv_steps_volpt": _SCENARIO_IV_STEPS_VOLPT,
        "by_spot": [], "by_iv": [], "n_positions": 0,
    }
    if not open_positions:
        return base_payload

    # Spot proxy : prefer a FUT marketPrice, fallback to an option strike.
    current_spot: float | None = None
    for p in open_positions:
        spec = parse_local_symbol(p.structure)
        if spec and spec.instrument_type == "FUTURE" and p.market_price:
            current_spot = float(p.market_price)
            break
    if current_spot is None:
        for p in open_positions:
            spec = parse_local_symbol(p.structure)
            if spec and spec.option_type and spec.strike and p.market_price:
                current_spot = float(spec.strike)
                break
    if current_spot is None:
        return base_payload

    today = datetime.now(UTC).date()
    positions_resolved: list[dict[str, Any]] = []
    iv_sum = 0.0
    iv_count = 0
    for p in open_positions:
        spec = parse_local_symbol(p.structure)
        if spec is None:
            continue
        qty_signed = float(p.quantity) * (1.0 if p.side == "BUY" else -1.0)
        mult = spec.multiplier
        if spec.instrument_type == "FUTURE":
            positions_resolved.append({
                "type": "FUTURE", "qty_signed": qty_signed, "mult": mult,
                "npv_base": qty_signed * mult * current_spot,
            })
        elif spec.option_type and spec.strike and p.expiry and p.iv:
            T = max(0.001, (p.expiry - today).days / 365.0)
            iv_dec = float(p.iv)
            iv_sum += iv_dec
            iv_count += 1
            right = "C" if spec.option_type == "CALL" else "P"
            base_price = bs_price(current_spot, spec.strike, T, iv_dec, right)
            positions_resolved.append({
                "type": "OPTION", "qty_signed": qty_signed, "mult": mult,
                "K": float(spec.strike), "T": T, "iv": iv_dec, "right": right,
                "npv_base": qty_signed * mult * base_price,
            })

    current_iv_avg_pct = (iv_sum / iv_count * 100.0) if iv_count > 0 else None

    def _aggregate(spot_now: float, iv_shift_dec: float) -> dict[str, float]:
        """Revalue all positions at shocked (spot, iv) ; return totals."""
        pnl = delta = gamma = vega = theta = 0.0
        for pos in positions_resolved:
            if pos["type"] == "FUTURE":
                npv = pos["qty_signed"] * pos["mult"] * spot_now
                delta += pos["qty_signed"] * pos["mult"]
                pnl += npv - pos["npv_base"]
                continue
            K, T = pos["K"], pos["T"]
            sigma = max(0.001, pos["iv"] + iv_shift_dec)
            right = pos["right"]
            price = bs_price(spot_now, K, T, sigma, right)
            npv = pos["qty_signed"] * pos["mult"] * price
            pnl += npv - pos["npv_base"]
            n_mult = pos["qty_signed"] * pos["mult"]
            delta += n_mult * bs_delta(spot_now, K, T, sigma, right)
            gamma += n_mult * bs_gamma(spot_now, K, T, sigma) * 1e-4   # $/pip
            vega  += n_mult * bs_vega(spot_now, K, T, sigma) * 0.01    # $/vol-pt
            theta += n_mult * bs_theta(spot_now, K, T, sigma, right) / 365.0
        return {
            "pnl_usd": round(pnl, 2),
            "delta_usd": round(delta, 2),
            "gamma_usd_per_pip": round(gamma, 2),
            "vega_usd_per_volpt": round(vega, 2),
            "theta_usd_per_day": round(theta, 2),
        }

    by_spot: list[dict[str, Any]] = []
    for step_pct in _SCENARIO_SPOT_STEPS_PCT:
        shocked = current_spot * (1.0 + step_pct / 100.0)
        agg = _aggregate(spot_now=shocked, iv_shift_dec=0.0)
        by_spot.append({"step_pct": step_pct, "spot": round(shocked, 5), **agg})

    by_iv: list[dict[str, Any]] = []
    for step_vp in _SCENARIO_IV_STEPS_VOLPT:
        agg = _aggregate(spot_now=current_spot, iv_shift_dec=step_vp / 100.0)
        by_iv.append({"step_vp": step_vp, **agg})

    return {
        "current_spot": round(current_spot, 5),
        "current_iv_avg_pct": (round(current_iv_avg_pct, 2)
                               if current_iv_avg_pct is not None else None),
        "spot_steps_pct": _SCENARIO_SPOT_STEPS_PCT,
        "iv_steps_volpt": _SCENARIO_IV_STEPS_VOLPT,
        "by_spot": by_spot,
        "by_iv": by_iv,
        "n_positions": len(positions_resolved),
    }


# ───────────────────────── P4 — Trade holdings + performance (R11 G) ─────────


# The percentile, the sign and the clamps live in core.risk.var_stats (#316).
_percentile = quantile


# Max calendar-day span for two net-liq samples to count as a 1-day P&L delta.
# 3 absorbs Fri→Mon weekends; anything longer is a data gap / capital move.
_VAR_MAX_GAP_DAYS = 3
# Below this the empirical quantiles are noise, whatever the method.
_VAR_MIN_OBS = MIN_OBS
# Historical simulation has a full year of bars to draw on, so hold it to a much
# higher bar than the account-history fallback: a 99% quantile off a part-filled
# cache (a few weeks of bars) would be a worse number than no number at all.
_HIST_SIM_MIN_SCENARIOS = 60


def _var_stats(deltas: list[float]) -> dict[str, float] | None:
    """Historical 1d VaR 95/99 + ES 99 from a P&L series, through the one
    helper (``core.risk.var_stats``): finite values only, positive losses
    clamped and ordered there, turned into P&L quantiles (negative = loss)
    here because ``/var`` plots them on the P&L axis. ``None`` when fewer than
    ``_VAR_MIN_OBS`` observations."""
    summary = var_summary(deltas, min_obs=_VAR_MIN_OBS)
    if summary is None:
        return None
    return {"var_95": -summary.var_95, "var_99": -summary.var_99, "es_99": -summary.es_99, "n": float(summary.n)}


def _histogram(values: list[float], nbins: int = 0) -> list[dict[str, float]]:
    """Equal-width histogram bins ``[{lo, hi, count}]`` over `values`. Empty
    when < 2 points or zero range. ``nbins<=0`` ⇒ Sturges' rule
    (``1 + ⌈log2 n⌉``, clamped 5..21) so sparse series don't shatter into many
    empty bins."""
    if len(values) < 2:
        return []
    lo, hi = min(values), max(values)
    if hi <= lo:
        return []
    if nbins <= 0:
        nbins = max(5, min(21, 1 + len(values).bit_length()))
    width = (hi - lo) / nbins
    counts = [0] * nbins
    for v in values:
        idx = min(nbins - 1, int((v - lo) / width))
        counts[idx] += 1
    return [
        {"lo": round(lo + i * width, 2), "hi": round(lo + (i + 1) * width, 2), "count": counts[i]}
        for i in range(nbins)
    ]


def _sharpe_and_drawdown(nl: list[float]) -> tuple[float | None, float | None, float | None]:
    """Pure compute (unit-tested): from a daily net-liq curve → annualised
    Sharpe, max drawdown (≤0), and current drawdown vs the running peak (≤0).
    All ``None`` when the series is too short (< 3 points)."""
    if len(nl) < 3:
        return None, None, None
    rets = [(nl[i] - nl[i - 1]) / nl[i - 1] for i in range(1, len(nl)) if nl[i - 1]]
    sharpe: float | None = None
    if rets:
        mean = sum(rets) / len(rets)
        std = (sum((x - mean) ** 2 for x in rets) / len(rets)) ** 0.5
        sharpe = (mean / std) * (252 ** 0.5) if std > 0 else None
    peak = nl[0]
    max_dd = 0.0
    for v in nl:
        peak = max(peak, v)
        if peak > 0:
            max_dd = min(max_dd, (v - peak) / peak)
    run_peak = max(nl)
    current_dd = (nl[-1] - run_peak) / run_peak if run_peak > 0 else None
    return sharpe, max_dd, current_dd


def _coerce_float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _currency_cash(balance: Any) -> float:
    """Settled cash for one account-currency entry. IB stores a dict of tags per
    currency (``CashBalance`` = settled); some snapshots / legacy rows stored a
    bare number. Returns 0.0 when unparseable — never raises (this used to crash
    on ``float(dict)``). (``ExchangeRate`` is to the account *base*, not USD, so
    it isn't used for the USD valuation here — EUR uses the EURUSD spot.)
    """
    if isinstance(balance, dict):
        return _coerce_float(balance.get("CashBalance")) or 0.0
    return _coerce_float(balance) or 0.0


@router.get("/cash")
async def cash_holdings(db: DbDep) -> dict[str, Any]:
    """Per-currency cash detail for the Trade holdings donut (R11 G-trade).

    Source : the latest ``account_history.currencies`` JSONB (currency → settled
    cash balance, written by the execution-engine). USD value uses the latest
    Per-currency cash from the latest ``account_history`` snapshot. Each entry is
    IB's tag dict (``CashBalance`` = settled). USD is 1:1; EUR converts via the
    EURUSD surface spot; any other currency → ``usd_value=None``. Unsettled cash
    isn't tracked upstream → always ``None``.
    """
    latest = (await db.execute(
        select(AccountHistory).order_by(desc(AccountHistory.timestamp)).limit(1)
    )).scalar_one_or_none()
    spot_row = (await db.execute(
        select(VolSurface.spot).where(VolSurface.underlying == "EURUSD")
        .order_by(desc(VolSurface.timestamp)).limit(1)
    )).scalar_one_or_none()
    eurusd = float(spot_row) if spot_row is not None else None

    currencies = (latest.currencies if latest and latest.currencies else {}) or {}
    rows: list[dict[str, Any]] = []
    for ccy, balance in currencies.items():
        settled = _currency_cash(balance)
        if ccy == "USD":
            rate: float | None = 1.0
            usd: float | None = settled
        elif ccy == "EUR" and eurusd is not None:
            rate, usd = eurusd, settled * eurusd
        else:
            rate, usd = None, None
        rows.append({
            "ccy": ccy, "settled": settled, "unsettled": None,
            "rate": rate, "usd_value": (round(usd, 2) if usd is not None else None),
        })
    # Largest USD value first; unconvertible currencies last.
    rows.sort(key=lambda r: (r["usd_value"] is None, -(r["usd_value"] or 0.0)))
    total_usd = sum(r["usd_value"] for r in rows if r["usd_value"] is not None)
    return {
        "timestamp": latest.timestamp.isoformat() if latest and latest.timestamp else None,
        "eurusd_spot": eurusd,
        "currencies": rows,
        "total_usd": round(total_usd, 2),
        "freshness": _freshness(latest.timestamp if latest else None),
    }


# The api never pays for a full rebuild on the request path: when the rollup
# table is empty (engine not running yet) ONE bounded refresh per
# _ROLLUP_FALLBACK_EVERY_S per process seeds it, index probes only.
_ROLLUP_FALLBACK_EVERY_S = 600.0
_rollup_fallback_at: float = 0.0


async def _daily_rows_or_seed(db: AsyncSession, days: int) -> list[Any]:
    global _rollup_fallback_at
    rows = await read_portfolio_daily(db, days=days)
    if rows:
        return rows
    now_m = asyncio.get_running_loop().time()
    if now_m - _rollup_fallback_at < _ROLLUP_FALLBACK_EVERY_S:
        return rows
    _rollup_fallback_at = now_m
    logger.warning("portfolio_daily_empty_inline_refresh days=%s", min(days, BACKFILL_DAYS))
    try:
        await refresh_portfolio_daily(db, days=min(days, BACKFILL_DAYS))
    except Exception:
        logger.warning("portfolio_daily_inline_refresh_failed", exc_info=True)
        return rows
    return await read_portfolio_daily(db, days=days)


@router.get("/daily-pnl")
async def daily_pnl(db: DbDep, days: int = Query(90, ge=1, le=730)) -> dict[str, Any]:
    """Daily P&L — MARK-TO-MARKET (R11 G-portfolio).

    A vol trader holds positions for weeks, so "realized on close" reads flat while
    the book is open. The headline daily P&L is therefore the day-over-day change in
    EOD net-liquidation — the honest total P&L, footing to the equity curve.
    ``realized_usd`` (genuine closes only) is returned alongside for the
    realized/unrealized split, NOT as the bar height.

    Reads ``portfolio_daily`` (one row per UTC day, written by the analytics
    engine, #368); ``computed_at`` is the rollup's time.
    """
    rows = await _daily_rows_or_seed(db, days)
    series: list[dict[str, Any]] = []
    cum = 0.0
    for r in rows:
        mtm = float(r.mtm_usd) if r.mtm_usd is not None else 0.0
        cum += mtm
        series.append({
            "day": r.day.isoformat(),
            "mtm_usd": round(mtm, 2),
            "realized_usd": round(float(r.realized_usd or 0.0), 2),
            "cumulative_usd": round(cum, 2),
            "n_closed": int(r.n_closed or 0),
            "net_liq_close": float(r.net_liq_close) if r.net_liq_close is not None else None,
        })
    computed_at = max((r.computed_at for r in rows), default=None)
    return {
        "days": days, "series": series, "total_mtm_usd": round(cum, 2),
        "computed_at": computed_at.isoformat() if computed_at else None,
        "source": "portfolio_daily",
    }


# Non-greek P&L pivot axes → the trade_structure column each groups by. Whitelisted
# (never interpolate the client value into SQL). "trade" groups per structure id.
_PNL_PIVOT_COLS = {"structure": "structure_type", "tenor": "reference_tenor"}


@router.get("/pnl-attribution-pivot")
async def pnl_attribution_pivot(
    db: DbDep, by: str = Query("structure"), days: int = Query(90, ge=1, le=730),
) -> dict[str, Any]:
    """LIVE book P&L bridged by a NON-greek axis (structure type / tenor / trade).

    Attributes the OPEN book's current unrealized P&L (``open_position.current_pnl_usd``)
    grouped by the chosen ``trade_structure`` axis — realized-close attribution reads
    flat while positions net flat at IB, so the meaningful bridge for a live desk is
    the current book. ``by=trade`` gives one bar per structure, labelled ``#<id>`` with
    the structure type as the sub-label. ``by=structure`` and ``by=tenor`` return the
    rich Position-breakdown shape (P&L + nominal/vega + vanna + volga per group) for a
    tabular view. Steps are USD; the frontend scales to $k.
    (``by greek`` is the Taylor decomposition on /pnl-attribution; ``by mode`` (PCA)
    is a separate research feature.)
    """
    if by == "trade":
        sql = text("""
            SELECT ts.id AS gid, ts.structure_type AS stype,
                   COALESCE(SUM(op.current_pnl_usd), 0) AS pnl,
                   COUNT(*) AS n
              FROM open_position op
              JOIN trade_structure ts ON op.trade_id = ts.id
             GROUP BY ts.id, ts.structure_type
            HAVING COALESCE(SUM(op.current_pnl_usd), 0) <> 0
             ORDER BY pnl DESC
        """)
        rows = (await db.execute(sql)).all()
        groups = [
            {"label": f"#{int(r.gid)}", "sub": str(r.stype or ""),
             "pnl_usd": round(float(r.pnl or 0.0), 2), "n": int(r.n)}
            for r in rows
        ]
    elif by == "structure":
        # Rich per-structure-type breakdown: P&L + nominal + 2nd-order greeks, for
        # a Position-breakdown-style table (Structure | P&L% | Nominal% | Vanna | Volga).
        sql = text("""
            SELECT COALESCE(ts.structure_type, 'other') AS grp,
                   COALESCE(SUM(op.current_pnl_usd), 0) AS pnl,
                   COALESCE(SUM(op.nominal_eur), 0) AS nominal,
                   COALESCE(SUM(op.vanna_usd), 0) AS vanna,
                   COALESCE(SUM(op.volga_usd), 0) AS volga,
                   COUNT(*) AS n
              FROM open_position op
              JOIN trade_structure ts ON op.trade_id = ts.id
             GROUP BY 1
            HAVING COALESCE(SUM(op.current_pnl_usd), 0) <> 0
                OR COALESCE(SUM(op.nominal_eur), 0) <> 0
             ORDER BY nominal DESC
        """)
        rows = (await db.execute(sql)).all()
        groups = [
            {"label": str(r.grp),
             "pnl_usd": round(float(r.pnl or 0.0), 2),
             "nominal_eur": round(float(r.nominal or 0.0), 2),
             "vanna_usd": round(float(r.vanna or 0.0), 2),
             "volga_usd": round(float(r.volga or 0.0), 2),
             "n": int(r.n)}
            for r in rows
        ]
        return {
            "by": by, "groups": groups,
            "total_usd": round(sum(g["pnl_usd"] for g in groups), 2),
            "total_nominal_eur": round(sum(g["nominal_eur"] for g in groups), 2),
        }
    elif by == "tenor":
        # Rich per-reference-tenor breakdown: P&L + vega + 2nd-order greeks on the
        # SAME axis, for a Position-breakdown-style table (Tenor | P&L% | Vega% |
        # Vanna | Volga). Full tenor ladder incl. buckets at 0 so it reads as a
        # complete term-structure.
        sql = text("""
            SELECT COALESCE(ts.reference_tenor, 'other') AS grp,
                   COALESCE(SUM(op.current_pnl_usd), 0) AS pnl,
                   COALESCE(SUM(op.nominal_eur), 0) AS nominal,
                   COALESCE(SUM(op.delta_usd), 0) AS delta,
                   COALESCE(SUM(op.gamma_usd), 0) AS gamma,
                   COALESCE(SUM(op.vega_usd), 0) AS vega,
                   COALESCE(SUM(op.theta_usd), 0) AS theta,
                   COALESCE(SUM(op.vanna_usd), 0) AS vanna,
                   COALESCE(SUM(op.volga_usd), 0) AS volga,
                   COUNT(*) AS n
              FROM open_position op
              JOIN trade_structure ts ON op.trade_id = ts.id
             GROUP BY 1
        """)
        rows = (await db.execute(sql)).all()
        by_grp = {
            str(r.grp): {
                "pnl_usd": round(float(r.pnl or 0.0), 2),
                "nominal_eur": round(float(r.nominal or 0.0), 2),
                "delta_usd": round(float(r.delta or 0.0), 2),
                "gamma_usd": round(float(r.gamma or 0.0), 2),
                "vega_usd": round(float(r.vega or 0.0), 2),
                "theta_usd": round(float(r.theta or 0.0), 2),
                "vanna_usd": round(float(r.vanna or 0.0), 2),
                "volga_usd": round(float(r.volga or 0.0), 2),
                "n": int(r.n),
            }
            for r in rows
        }
        ladder = ["1M", "2M", "3M", "4M", "5M", "6M"]
        extra = sorted(g for g in by_grp if g not in ladder)
        zero = {
            "pnl_usd": 0.0, "nominal_eur": 0.0, "delta_usd": 0.0, "gamma_usd": 0.0,
            "vega_usd": 0.0, "theta_usd": 0.0, "vanna_usd": 0.0, "volga_usd": 0.0, "n": 0,
        }
        groups = [{"label": t, **by_grp.get(t, zero)} for t in ladder + extra]
        return {
            "by": by, "groups": groups,
            "total_usd": round(sum(g["pnl_usd"] for g in groups), 2),
            "total_nominal_eur": round(sum(g["nominal_eur"] for g in groups), 2),
            "total_vega_usd": round(sum(g["vega_usd"] for g in groups), 2),
        }
    else:
        raise HTTPException(400, f"unknown pivot '{by}' (expected {[*_PNL_PIVOT_COLS, 'trade']})")
    total = round(sum(g["pnl_usd"] for g in groups), 2)
    return {"by": by, "groups": groups, "total_usd": total}


async def _daily_closes(redis: aioredis.Redis, symbol: str = "EURUSD") -> list[float]:
    """Year of daily closes from the market-data engine's bar cache (``bars:…:1Y``).

    Empty when the engines are down / the cache has not been populated yet — the
    caller then falls back to the account-history VaR."""
    try:
        raw = await redis.get(keys.BARS.format(symbol=symbol, timeframe="1Y"))
    except Exception:
        logger.warning("var_bars_cache_unavailable", exc_info=True)
        return []
    if not raw:
        return []
    try:
        rows = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("var_bars_cache_corrupt")
        return []
    return [float(r["c"]) for r in rows if isinstance(r, dict) and r.get("c")]


async def _hist_sim(db: AsyncSession, redis: aioredis.Redis) -> dict[str, Any] | None:
    """Replay a year of daily EURUSD moves through the current book.

    Returns the portfolio P&L vector, the per-position vectors keyed by open
    position id, and scenario metadata — or ``None`` when the book or the bar
    cache is missing, i.e. when there is nothing to simulate."""
    book = await _resolve_book(db, redis)
    spot, baselines, spot_source = book.spot, book.baselines, book.spot_source
    if spot is None or not baselines:
        return None
    shocks = market_shocks(await _daily_closes(redis))
    if len(shocks) < _HIST_SIM_MIN_SCENARIOS:
        return None
    by_position = simulate_pnl_by_position(baselines, spot, shocks)
    return {
        "pnl": portfolio_pnl(by_position),
        "by_id": {str(b["id"]): vec for b, vec in zip(baselines, by_position, strict=True)},
        "meta": {
            "current_spot": round(spot, 5),
            "spot_source": spot_source, "dropped": book.dropped, "fallback": book.fallback,
            "n_positions": len(baselines),
            "n_scenarios": len(shocks),
        },
    }


@router.get("/var")
async def value_at_risk(db: DbDep, redis: RedisDep) -> dict[str, Any]:
    """1-day Value-at-Risk — VaR 95 / 99 + ES 99, as losses (negative USD).

    Two methods, in order of preference:

    ``historical-simulation`` (preferred) replays ~1 year of real EURUSD daily
    moves through **today's** book, full-BS revalued per scenario (see
    ``core.risk.hist_var``). Available from the first open position, and it
    measures the book that is actually on right now.

    ``account-history`` (fallback, used when the book or the bar cache is empty)
    is the empirical distribution of daily ``net_liq`` changes over the last
    ~504 sessions. It describes the book as it *was*, and needs 5 daily
    observations before it says anything — fields stay ``None`` until then.

    ``hist`` is the histogram of whichever distribution was used, so the desk's
    P&L-distribution chart always plots the same population the quantiles came from.
    """
    sim = await _hist_sim(db, redis)
    if sim is not None:
        pnl = finite(sim["pnl"])
        stats = _var_stats(pnl)
        return {
            "computed_at": datetime.now(UTC).isoformat(),
            "method": "historical-simulation",
            "convention": "pnl_quantile",
            "horizon_days": 1,
            "n_days": len(pnl),
            "mean_daily_usd": round(sum(pnl) / len(pnl), 2) if pnl else None,
            "var_95_usd": round(stats["var_95"], 2) if stats else None,
            "var_99_usd": round(stats["var_99"], 2) if stats else None,
            "es_99_usd": round(stats["es_99"], 2) if stats else None,
            "hist": _histogram(pnl),
            **sim["meta"],
        }
    # Portfolio settings (editable): VaR lookback window + max day-gap.
    pf = {name: float(val) for name, val in (await db.execute(
        select(AppConfigScalar.name, AppConfigScalar.value)
        .where(AppConfigScalar.namespace == "portfolio")
    )).all()}
    lookback = int(pf.get("var_lookback_days", 504))
    max_gap = int(pf.get("var_max_gap_days", _VAR_MAX_GAP_DAYS))
    # Daily closes from the materialised rollup (#368), never a per-day scan.
    nav, rollup_at = await read_nav_series(db, days=lookback)
    # Only consecutive trading-day samples form a genuine 1-day P&L delta. A
    # multi-day hole (system downtime, capital deposit/withdrawal) is NOT a daily
    # loss — including it injects a huge phantom tail that dominates VaR/ES and
    # the histogram. Skip any step spanning more than the configured gap.
    deltas = finite(
        nav[i][1] - nav[i - 1][1]
        for i in range(1, len(nav))
        if (nav[i][0] - nav[i - 1][0]).days <= max_gap
    )
    stats = _var_stats(deltas)
    mean_daily = sum(deltas) / len(deltas) if deltas else None
    return {
        "computed_at": (rollup_at or datetime.now(UTC)).isoformat(),
        "method": "account-history",
        "convention": "pnl_quantile",
        "horizon_days": 1,
        "window_days": lookback,
        "dropped": [],
        "fallback": {},
        "n_days": len(deltas),
        # live mean daily P&L → the table's expected-return column (× horizon),
        # no longer a hardcoded assumed return.
        "mean_daily_usd": round(mean_daily, 2) if mean_daily is not None else None,
        "var_95_usd": round(stats["var_95"], 2) if stats else None,
        "var_99_usd": round(stats["var_99"], 2) if stats else None,
        "es_99_usd": round(stats["es_99"], 2) if stats else None,
        "hist": _histogram(deltas),
    }


@router.get("/greek-limits")
async def greek_limits(db: DbDep) -> dict[str, Any]:
    """Derived greek caps from the stress-loss budget (docs/strategy/greek-limits.md §2/§6/§8).

    Caps are *computed, not configured*: ``L* = ALPHA·nav_base`` is projected
    onto delta/vega/gamma/cross by inverting each axis' shock. ``nav_base`` is
    the slow anchor (0.9·high-water-mark ∨ EWMA-20d of the daily net-liq series)
    so a drawdown does not procyclically tighten every cap at once. The live NAV
    is returned for display only. ``regime_mult`` (§8) scales the caps down as the
    prevailing vol rises above its recent typical level. Fields are 0 until
    ~enough net-liq history + a spot exist.
    """
    # Same computation as the pre-trade gate (#315): api.orchestration.greek_caps.
    live = await compute_live_caps(db)
    caps, nav_series = live.caps, live.nav_series
    return {
        "computed_at": datetime.now(UTC).isoformat(),
        "nav_base_usd": round(caps.nav_base_usd, 2),
        "nav_live_usd": round(nav_series[-1], 2) if nav_series else None,
        "spot": caps.spot or None,
        "regime_mult": caps.regime_mult,
        "alpha": gl.ALPHA,
        "loss_budget_usd": round(caps.loss_budget_usd, 2),
        "delta_cap_usd": round(caps.delta_usd, 2),
        "vega_cap_usd": round(caps.vega_usd, 2),
        "gamma_cap_pip": round(caps.gamma_pip, 2),
        "cross_budget_usd": round(caps.cross_usd, 2),
    }


@router.get("/stats")
async def portfolio_stats(db: DbDep) -> dict[str, Any]:
    """Headline performance stats (R11 G-portfolio).

    Sharpe + drawdown from the daily net-liq closes of ``portfolio_daily``
    (materialised, #368), hit-rate + cumulative realized from closed booked
    positions, cumulative unrealized from the live book. Fields are ``None``
    when the underlying series is too short / empty (read-only public
    deployment at boot). ``computed_at`` is the rollup's time.
    """
    rows = await _daily_rows_or_seed(db, 365)
    nl = [float(r.net_liq_close) for r in rows if r.net_liq_close is not None]
    rollup_at = max((r.computed_at for r in rows), default=None)
    sharpe, max_dd, current_dd = _sharpe_and_drawdown(nl)
    # GENUINE closes only for realized / hit-rate: a real close has net_pnl_usd
    # computed by the finaliser. A position that netted flat at IB is auto-closed by
    # reconciliation with net_pnl_usd NULL (close_reason 'reconciled_flat_at_ib') —
    # it's a book-vs-broker adjustment, NOT a trade, so counting it would poison the
    # hit-rate (all-zero) and realized total. Track those separately for transparency.
    closed = (await db.execute(text("""
        SELECT COUNT(*) FILTER (WHERE net_pnl_usd IS NOT NULL) AS n,
               COUNT(*) FILTER (WHERE net_pnl_usd > 0) AS wins,
               COALESCE(SUM(net_pnl_usd), 0) AS cum_real,
               COUNT(*) FILTER (WHERE close_reason = 'reconciled_flat_at_ib') AS n_recon
          FROM booked_position WHERE state = 'closed'
    """))).one()
    n_closed = int(closed.n or 0)
    hit_rate = (float(closed.wins) / n_closed) if n_closed else None
    openp = (await db.execute(text(
        "SELECT COALESCE(SUM(current_pnl_usd), 0) AS u, COUNT(*) AS n FROM open_position"
    ))).one()
    # Ground-truth account change over the series (net-liq is the source of truth).
    # The frontend foots it: Δnet-liq ≈ realized + Δunrealized + (reconciliation gap).
    net_liq_change = (nl[-1] - nl[0]) if len(nl) >= 2 else None
    return {
        "computed_at": (rollup_at or datetime.now(UTC)).isoformat(),
        "sharpe": round(sharpe, 3) if sharpe is not None else None,
        "max_drawdown_pct": round(max_dd * 100, 2) if max_dd is not None else None,
        "current_drawdown_pct": round(current_dd * 100, 2) if current_dd is not None else None,
        "hit_rate": round(hit_rate, 4) if hit_rate is not None else None,
        "n_closed": n_closed,                       # genuine trade closes only
        "n_reconciled_flat": int(closed.n_recon or 0),  # netting/reconciliation adjustments
        "cum_realized_usd": round(float(closed.cum_real), 2),
        "cum_unrealized_usd": round(float(openp.u), 2),
        "net_liq_change_usd": round(net_liq_change, 2) if net_liq_change is not None else None,
        "n_open": int(openp.n or 0),
        "n_days": len(nl),
    }

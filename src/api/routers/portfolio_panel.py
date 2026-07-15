"""Portfolio panel — account-level aggregate views (cf. PORTFOLIO_PANEL.md).

Bounded context distinct du legacy `portfolio.py` (qui sert l'ancien
Portfolio panel based on the `positions` table). Ici on expose :

  GET /api/v1/portfolio/account            — latest + prev (≈24h) account_snap
  GET /api/v1/portfolio/equity-curve       — net_liq series, adaptive downsample
  GET /api/v1/portfolio/aggregate-greeks   — Σ Δ Γ V Θ across open positions
  GET /api/v1/portfolio/vega-per-tenor     — vega bucketed by DTE
  GET /api/v1/portfolio/hedge-summary      — multi-window cumul of hedge_orders

P1 + P2 + P3 shipped.
"""
from __future__ import annotations

import statistics
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db_session
from core.pricing.bs import bs_delta, bs_gamma, bs_price, bs_theta, bs_vega
from core.risk import greek_limits as gl
from core.risk.marginal_var import component_var
from core.risk.stress import reval_book
from core.risk.var_factors import factor_var_breakdown
from core.risk.vega_pca import N_CELLS, PC_NAMES, cell_index, project_vega
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
from shared.contracts import parse_local_symbol

router = APIRouter(prefix="/api/v1/portfolio", tags=["portfolio-panel"])
DbDep = Annotated[AsyncSession, Depends(get_db_session)]

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
    if s is None:
        return None
    return {
        "timestamp": s.timestamp.isoformat() if s.timestamp else None,
        "net_liq_usd": float(s.net_liq_usd) if s.net_liq_usd is not None else None,
        "cash_usd": float(s.cash_usd) if s.cash_usd is not None else None,
        "unrealized_pnl_usd": float(s.unrealized_pnl_usd) if s.unrealized_pnl_usd is not None else None,
        "gross_position_value": float(s.gross_position_value) if s.gross_position_value is not None else None,
        "init_margin_req": float(s.init_margin_req) if s.init_margin_req is not None else None,
        "maint_margin_req": float(s.maint_margin_req) if s.maint_margin_req is not None else None,
        "excess_liquidity": float(s.excess_liquidity) if s.excess_liquidity is not None else None,
        "cushion": float(s.cushion) if s.cushion is not None else None,
        "open_positions_count": s.open_positions_count,
        "currencies": s.currencies or {},
    }


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
async def header_summary(db: DbDep) -> dict[str, Any]:
    """One-shot endpoint for the dashboard sticky header (panel A).

    Bundles in a single round-trip :
      - latest ``account_snaps`` row + reference 24 h before for delta P&L
      - aggregate greeks across all OPEN positions (denormalised on
        ``positions`` since migration 028)
      - 1-day 99% historical VaR computed on the daily distribution of
        ``net_liq`` deltas from ``account_snaps`` (last 60 days).

    Frontend can render the whole panel-A strip from one fetch.
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

    # Historical VaR 1d 99% : take one ``net_liq`` value per UTC day over
    # the last 60 days, derive day-over-day deltas, return the 1st-percentile
    # (= worst-case loss with 99% confidence). NULL if < 5 days of data.
    var_sql = text("""
        WITH daily AS (
          SELECT DISTINCT ON (date_trunc('day', timestamp))
                 date_trunc('day', timestamp) AS day,
                 net_liq_usd
            FROM account_history
           WHERE timestamp >= NOW() - INTERVAL '60 days'
             AND net_liq_usd IS NOT NULL
           ORDER BY date_trunc('day', timestamp), timestamp DESC
        )
        SELECT net_liq_usd FROM daily ORDER BY day
    """)
    daily_nl = [r[0] for r in (await db.execute(var_sql)).all()]
    var_1d_99: float | None = None
    n_days = max(0, len(daily_nl) - 1)
    if n_days >= 5:
        deltas = [
            float(daily_nl[i] - daily_nl[i - 1])
            for i in range(1, len(daily_nl))
        ]
        deltas.sort()
        # 1st percentile via linear interpolation between the closest 2 ranks.
        rank = 0.01 * (len(deltas) - 1)
        lo = int(rank)
        hi = min(lo + 1, len(deltas) - 1)
        var_1d_99 = deltas[lo] + (rank - lo) * (deltas[hi] - deltas[lo])

    nl_now = float(latest.net_liq_usd) if latest and latest.net_liq_usd else None
    nl_prev = float(prev.net_liq_usd) if prev and prev.net_liq_usd else None
    pnl_24h = (nl_now - nl_prev) if (nl_now is not None and nl_prev is not None) else None

    init_m = float(latest.init_margin_req) if latest and latest.init_margin_req else None
    util_pct = (init_m / nl_now) if (init_m and nl_now) else None

    return {
        "computed_at": datetime.now(UTC).isoformat(),
        "account": {
            "net_liq_usd": nl_now,
            "cash_usd": float(latest.cash_usd) if latest and latest.cash_usd else None,
            "init_margin_req": init_m,
            "excess_liquidity": float(latest.excess_liquidity) if latest and latest.excess_liquidity else None,
            "cushion": float(latest.cushion) if latest and latest.cushion else None,
            "util_pct": util_pct,
            "n_open_positions": int(g.n_open or 0),
        },
        "pnl": {
            "total_24h_usd": pnl_24h,
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


async def _resolve_book(db: AsyncSession) -> tuple[float | None, list[dict[str, Any]]]:
    """Resolve OPEN positions → (current_spot, baselines) for ``reval_book``.

    Spot proxy : any FUTURE market_price, else an option strike (ATM fallback).
    Each option baseline stores the per-unit BS ``price_base`` at current iv.
    """
    open_positions = (await db.execute(select(OpenPosition))).scalars().all()
    if not open_positions:
        return None, []

    current_spot: float | None = None
    for p in open_positions:
        spec = parse_local_symbol(p.structure)
        if spec and spec.instrument_type == "FUTURE" and p.market_price:
            current_spot = float(p.market_price)
            break
    if current_spot is None:
        for p in open_positions:
            spec = parse_local_symbol(p.structure)
            if p.market_price and p.iv and spec and spec.strike:
                current_spot = float(spec.strike)
                break
    if current_spot is None:
        return None, []

    today = datetime.now(UTC).date()
    baselines: list[dict[str, Any]] = []
    for p in open_positions:
        spec = parse_local_symbol(p.structure)
        if spec is None:
            continue
        qty_signed = float(p.quantity) * (1.0 if p.side == "BUY" else -1.0)
        if spec.instrument_type == "FUTURE":
            baselines.append({"type": "FUTURE", "qty_signed": qty_signed, "mult": spec.multiplier})
        elif spec.option_type and spec.strike and p.expiry and p.iv:
            T = max(0.001, (p.expiry - today).days / 365.0)
            iv_dec = float(p.iv)
            right = "C" if spec.option_type == "CALL" else "P"
            baselines.append({
                "type": "OPTION", "qty_signed": qty_signed, "mult": spec.multiplier,
                "K": float(spec.strike), "T": T, "iv": iv_dec, "right": right,
                "price_base": bs_price(current_spot, float(spec.strike), T, iv_dec, right),
            })
    return current_spot, baselines


@router.get("/stress-grid")
async def stress_grid(
    db: DbDep,
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
    current_spot, baselines = await _resolve_book(db)
    base = {
        "current_spot": round(current_spot, 5) if current_spot else None,
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
    axis: str = Query("spot", pattern="^(spot|vol|time|skew|fly)$"),
) -> dict[str, Any]:
    """Per-bin greeks ladder along one axis (R11 G-risk 5.3). Each row = P&L +
    Δ/Γ/Vega revalued at that shock. ``hedge_delta_usd = −delta_usd``. The
    ``axis`` column name (``dspot_bps``/``dvol_vps``/…) reflects the chosen axis."""
    bins, kw, unit = _LADDER_AXES[axis]
    current_spot, baselines = await _resolve_book(db)
    base = {
        "current_spot": round(current_spot, 5) if current_spot else None,
        "axis": axis, "bins": bins, "unit": unit, "n_positions": len(baselines),
    }
    if axis == "spot":
        base["spot_bins_bps"] = _LADDER_SPOT_BPS  # legacy field
    if current_spot is None:
        return {**base, "rows": []}

    rows: list[dict[str, Any]] = []
    for b in bins:
        kwargs = {kw: float(b)}
        delta = reval_book(baselines, current_spot, output="delta", **kwargs)
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
async def vega_pca(db: DbDep) -> dict[str, Any]:
    """Project the book's per-cell vega onto the active PCA loadings (R11 G-risk).

    Each open option is classified into a 30-dim grid cell (DTE -> tenor, BS delta
    -> delta bucket) and its vega ($/vol-pt) accumulated. The active model's
    loadings + stds then give the book's vega P&L sensitivity to each PC
    (level / slope / curvature) -- see ``core.risk.vega_pca``.
    """
    current_spot, baselines = await _resolve_book(db)
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
async def marginal_var(db: DbDep) -> dict[str, Any]:
    """Per-position component VaR over the open book (R11 G-risk).

    Builds each open position's daily P&L delta series from
    ``open_position_history``, then decomposes the 99% historical portfolio VaR
    into per-position standalone + component contributions (Euler allocation, see
    ``core.risk.marginal_var``). The factor tag is the position's dominant greek
    (spot / level / skew / curv). Empty until ~5 days of history accumulate.
    """
    positions = (await db.execute(select(OpenPosition))).scalars().all()
    if not positions:
        return {"positions": [], "total": None, "n_days": 0}
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
    return {"positions": out, "total": total, "n_days": res["n_days"]}


@router.get("/var-factors")
async def var_factors(db: DbDep) -> dict[str, Any]:
    """Scenario VaR by factor (spot / level / skew / curv) over the open book (R11 G-risk).

    Each factor's VaR is the book's loss under its 1-day 99% adverse move, full-BS
    revalued via ``core.risk.var_factors`` — derived entirely from the live book
    (the shock sizes are documented desk assumptions). Empty when the book/spot is missing.
    """
    spot, baselines = await _resolve_book(db)
    if spot is None or not baselines:
        return {"current_spot": round(spot, 5) if spot else None, "n_positions": 0, "factors": []}
    return {
        "current_spot": round(spot, 5),
        "n_positions": len(baselines),
        "factors": factor_var_breakdown(baselines, spot),
    }


# ──────────────────────────────────────────────────────────────────────
# Panel G — P&L attribution over a lookback window
# ──────────────────────────────────────────────────────────────────────


@router.get("/pnl-attribution")
async def pnl_attribution(
    db: DbDep,
    lookback_hours: int = Query(24, ge=1, le=168),  # 1h..7d
) -> dict[str, Any]:
    """Decompose realized P&L into greek contributions over the window.

    Per-position Taylor expansion :
        actual_pnl  = (pnl_now - pnl_then)
        delta_pnl   = δ_now × (spot_now - spot_then)
        gamma_pnl   = 0.5 × Γ_now × (spot_now - spot_then) ** 2
        vega_pnl    = V_now × (iv_now - iv_then)      [vol points]
        theta_pnl   = Θ_now × Δt_days
        residual    = actual_pnl - (delta + gamma + vega + theta)

    Frozen-greeks approximation (uses current greeks for both endpoints) —
    fine for short windows ≤ 1 day, less accurate over a week. The
    ``residual`` row captures the un-attributed drift so the operator can
    spot when the Taylor expansion stops being valid.

    Sources :
      - IB-live positions (``position`` table) : t-1 row in
        ``position_metric_history`` closest to ``now - lookback_hours``.
        Spot comes from the snapshot's ``market_price`` for the
        underlying FUT contract on the same symbol.
      - Booked positions (``booked_position``) : t-1 row in
        ``booked_position_metric_history``. Spot stored on the snapshot.
    """
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=lookback_hours)

    # 1. IB-live positions : current state from ``position`` + t-1 from
    #    ``position_metric_history``. Use a CTE that picks the row whose
    #    timestamp is the latest one still ≤ cutoff (closest from below).
    ib_sql = text("""
        WITH t1 AS (
          SELECT DISTINCT ON (position_id)
                 position_id,
                 timestamp,
                 market_price,
                 current_pnl_usd,
                 iv,
                 delta_usd, gamma_usd, vega_usd, theta_usd
            FROM open_position_history
           WHERE timestamp <= :cutoff
           ORDER BY position_id, timestamp DESC
        )
        SELECT p.id, p.structure, p.product_label, p.side,
               p.current_pnl_usd AS pnl_now,
               p.market_price    AS spot_now,
               p.iv              AS iv_now,
               p.delta_usd       AS delta_now,
               p.gamma_usd       AS gamma_now,
               p.vega_usd        AS vega_now,
               p.theta_usd       AS theta_now,
               t1.current_pnl_usd AS pnl_then,
               t1.market_price    AS spot_then,
               t1.iv              AS iv_then,
               t1.timestamp       AS t_then
          FROM open_position p
          LEFT JOIN t1 ON t1.position_id = p.id
    """)
    ib_rows = (await db.execute(ib_sql, {"cutoff": cutoff})).all()

    # 2. Booked positions : current state from latest metric_history row +
    #    t-1 from earlier row.
    booked_sql = text("""
        WITH latest AS (
          SELECT DISTINCT ON (position_id)
                 position_id, timestamp,
                 spot, iv_avg_legs_pct,
                 current_pnl_gross_usd, current_pnl_net_usd,
                 current_vega_usd_per_volpt,
                 current_gamma_usd_per_pip2,
                 current_theta_usd_per_day,
                 current_delta_unhedged
            FROM booked_position_metric_history
           ORDER BY position_id, timestamp DESC
        ),
        t1 AS (
          SELECT DISTINCT ON (position_id)
                 position_id, timestamp,
                 spot, iv_avg_legs_pct,
                 current_pnl_gross_usd
            FROM booked_position_metric_history
           WHERE timestamp <= :cutoff
           ORDER BY position_id, timestamp DESC
        )
        SELECT bp.id, bp.state,
               ts.product_label AS product_label,
               latest.current_pnl_gross_usd AS pnl_now,
               latest.spot                  AS spot_now,
               latest.iv_avg_legs_pct       AS iv_now,
               latest.current_delta_unhedged AS delta_now,
               latest.current_gamma_usd_per_pip2 AS gamma_now,
               latest.current_vega_usd_per_volpt AS vega_now,
               latest.current_theta_usd_per_day  AS theta_now,
               t1.current_pnl_gross_usd AS pnl_then,
               t1.spot                  AS spot_then,
               t1.iv_avg_legs_pct       AS iv_then,
               t1.timestamp             AS t_then
          FROM booked_position bp
          LEFT JOIN trade_structure ts ON ts.id = bp.structure_id
          LEFT JOIN latest ON latest.position_id = bp.id
          LEFT JOIN t1     ON t1.position_id     = bp.id
         WHERE bp.state = 'open'
    """)
    booked_rows = (await db.execute(booked_sql, {"cutoff": cutoff})).all()

    # 3. Decompose each row. None on any input → return Nones (caller
    #    displays "—") so partial data doesn't poison aggregates.
    def _decompose(
        pnl_now: float | None, pnl_then: float | None,
        spot_now: float | None, spot_then: float | None,
        iv_now: float | None, iv_then: float | None,
        delta: float | None, gamma: float | None,
        vega: float | None, theta: float | None,
        t_then: datetime | None,
    ) -> dict[str, float | None]:
        actual = (pnl_now - pnl_then) if (pnl_now is not None and pnl_then is not None) else None
        dspot = (spot_now - spot_then) if (spot_now is not None and spot_then is not None) else None
        div_pts = (iv_now - iv_then) if (iv_now is not None and iv_then is not None) else None
        dt_days = ((now - t_then).total_seconds() / 86400.0) if t_then is not None else None

        delta_pnl = (delta * dspot) if (delta is not None and dspot is not None) else None
        gamma_pnl = (0.5 * gamma * dspot * dspot) if (gamma is not None and dspot is not None) else None
        vega_pnl = (vega * div_pts) if (vega is not None and div_pts is not None) else None
        theta_pnl = (theta * dt_days) if (theta is not None and dt_days is not None) else None

        explained: float | None
        if None in (delta_pnl, gamma_pnl, vega_pnl, theta_pnl):
            explained = None
        else:
            explained = float(delta_pnl) + float(gamma_pnl) + float(vega_pnl) + float(theta_pnl)
        residual = (actual - explained) if (actual is not None and explained is not None) else None

        return {
            "actual_pnl_usd": round(actual, 2) if actual is not None else None,
            "delta_pnl_usd": round(delta_pnl, 2) if delta_pnl is not None else None,
            "gamma_pnl_usd": round(gamma_pnl, 2) if gamma_pnl is not None else None,
            "vega_pnl_usd": round(vega_pnl, 2) if vega_pnl is not None else None,
            "theta_pnl_usd": round(theta_pnl, 2) if theta_pnl is not None else None,
            "residual_usd": round(residual, 2) if residual is not None else None,
        }

    per_position: list[dict[str, Any]] = []
    for r in ib_rows:
        decomp = _decompose(
            pnl_now=float(r.pnl_now) if r.pnl_now is not None else None,
            pnl_then=float(r.pnl_then) if r.pnl_then is not None else None,
            spot_now=float(r.spot_now) if r.spot_now is not None else None,
            spot_then=float(r.spot_then) if r.spot_then is not None else None,
            iv_now=float(r.iv_now) if r.iv_now is not None else None,
            iv_then=float(r.iv_then) if r.iv_then is not None else None,
            delta=float(r.delta_now) if r.delta_now is not None else None,
            gamma=float(r.gamma_now) if r.gamma_now is not None else None,
            vega=float(r.vega_now) if r.vega_now is not None else None,
            theta=float(r.theta_now) if r.theta_now is not None else None,
            t_then=r.t_then,
        )
        per_position.append({
            "id": int(r.id), "source": "ib_live",
            "structure": r.structure, "product_label": r.product_label,
            "side": r.side,
            **decomp,
        })
    for r in booked_rows:
        decomp = _decompose(
            pnl_now=float(r.pnl_now) if r.pnl_now is not None else None,
            pnl_then=float(r.pnl_then) if r.pnl_then is not None else None,
            spot_now=float(r.spot_now) if r.spot_now is not None else None,
            spot_then=float(r.spot_then) if r.spot_then is not None else None,
            iv_now=float(r.iv_now) if r.iv_now is not None else None,
            iv_then=float(r.iv_then) if r.iv_then is not None else None,
            delta=float(r.delta_now) if r.delta_now is not None else None,
            gamma=float(r.gamma_now) if r.gamma_now is not None else None,
            vega=float(r.vega_now) if r.vega_now is not None else None,
            theta=float(r.theta_now) if r.theta_now is not None else None,
            t_then=r.t_then,
        )
        per_position.append({
            "id": int(r.id), "source": "booked",
            "structure": None, "product_label": r.product_label,
            "side": None,
            **decomp,
        })

    # 4. Aggregate. Sum across positions where the term is not None.
    def _sum(key: str) -> float | None:
        vals = [row[key] for row in per_position if row[key] is not None]
        return round(sum(vals), 2) if vals else None

    return {
        "lookback_hours": lookback_hours,
        "computed_at": now.isoformat(),
        "totals": {
            "actual_pnl_usd": _sum("actual_pnl_usd"),
            "delta_pnl_usd": _sum("delta_pnl_usd"),
            "gamma_pnl_usd": _sum("gamma_pnl_usd"),
            "vega_pnl_usd": _sum("vega_pnl_usd"),
            "theta_pnl_usd": _sum("theta_pnl_usd"),
            "residual_usd": _sum("residual_usd"),
        },
        "per_position": per_position,
    }


# ──────────────────────────────────────────────────────────────────────
# Panel J — Pin risk grid (full BS revaluation at strike ± breach)
# ──────────────────────────────────────────────────────────────────────


_BREACH_BPS = 50  # ±50 bp around the strike


@router.get("/pin-risk")
async def pin_risk(db: DbDep) -> dict[str, Any]:
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

    # Same spot proxy as stress-grid (most reliable from a held FUTURE).
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


def _percentile(sorted_vals: list[float], q: float) -> float | None:
    """Linear-interpolated q-quantile (q in [0,1]) of an already-sorted list."""
    if not sorted_vals:
        return None
    rank = q * (len(sorted_vals) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (rank - lo) * (sorted_vals[hi] - sorted_vals[lo])


# Max calendar-day span for two net-liq samples to count as a 1-day P&L delta.
# 3 absorbs Fri→Mon weekends; anything longer is a data gap / capital move.
_VAR_MAX_GAP_DAYS = 3


def _var_stats(deltas: list[float]) -> dict[str, float] | None:
    """Pure compute (unit-tested): historical 1d VaR 95/99 + ES 99 (mean of the
    losses at or below the 99% quantile) from a list of net-liq daily changes.
    `None` when < 5 observations. Values are losses (negative)."""
    if len(deltas) < 5:
        return None
    s = sorted(deltas)
    var95 = _percentile(s, 0.05)
    var99 = _percentile(s, 0.01)
    if var95 is None or var99 is None:
        return None
    tail = [x for x in s if x <= var99]
    es99 = sum(tail) / len(tail) if tail else var99
    return {"var_95": var95, "var_99": var99, "es_99": es99, "n": float(len(deltas))}


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


@router.get("/daily-pnl")
async def daily_pnl(db: DbDep, days: int = Query(90, ge=1, le=730)) -> dict[str, Any]:
    """Daily P&L — MARK-TO-MARKET (R11 G-portfolio).

    A vol trader holds positions for weeks, so "realized on close" reads flat while
    the book is open. The headline daily P&L is therefore the day-over-day change in
    EOD net-liquidation (``account_history``) — the honest total P&L, footing to the
    equity curve. ``realized_usd`` (genuine closes only) is returned alongside for the
    realized/unrealized split, NOT as the bar height.
    """
    # 1) MTM: last net-liq per UTC day → day-over-day delta.
    mtm_rows = (await db.execute(text("""
        WITH eod AS (
          SELECT DISTINCT ON (date_trunc('day', timestamp))
                 date_trunc('day', timestamp)::date AS day, net_liq_usd
            FROM account_history
           WHERE timestamp >= NOW() - make_interval(days => :days)
             AND net_liq_usd IS NOT NULL
           ORDER BY date_trunc('day', timestamp), timestamp DESC
        )
        SELECT day, net_liq_usd,
               net_liq_usd - LAG(net_liq_usd) OVER (ORDER BY day) AS mtm
          FROM eod ORDER BY day
    """), {"days": days})).all()
    # 2) Realized per day from GENUINE closes only (net_pnl_usd computed).
    real_rows = (await db.execute(text("""
        SELECT date_trunc('day', closed_at)::date AS day,
               COALESCE(SUM(net_pnl_usd), 0) AS realized,
               COUNT(*) FILTER (WHERE net_pnl_usd IS NOT NULL) AS n_closed
          FROM booked_position
         WHERE state = 'closed' AND closed_at IS NOT NULL
           AND closed_at >= NOW() - make_interval(days => :days)
         GROUP BY 1
    """), {"days": days})).all()
    real_by_day = {r.day.isoformat(): (float(r.realized or 0.0), int(r.n_closed)) for r in real_rows}

    series: list[dict[str, Any]] = []
    cum = 0.0
    for r in mtm_rows:
        mtm = float(r.mtm) if r.mtm is not None else 0.0
        cum += mtm
        day = r.day.isoformat()
        realized, n_closed = real_by_day.get(day, (0.0, 0))
        series.append({
            "day": day,
            "mtm_usd": round(mtm, 2),
            "realized_usd": round(realized, 2),
            "cumulative_usd": round(cum, 2),
            "n_closed": n_closed,
        })
    return {"days": days, "series": series, "total_mtm_usd": round(cum, 2)}


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
                   COALESCE(SUM(op.vega_usd), 0) AS vega,
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
                "vega_usd": round(float(r.vega or 0.0), 2),
                "vanna_usd": round(float(r.vanna or 0.0), 2),
                "volga_usd": round(float(r.volga or 0.0), 2),
                "n": int(r.n),
            }
            for r in rows
        }
        ladder = ["1M", "2M", "3M", "4M", "5M", "6M", "9M", "1Y"]
        extra = sorted(g for g in by_grp if g not in ladder)
        zero = {"pnl_usd": 0.0, "vega_usd": 0.0, "vanna_usd": 0.0, "volga_usd": 0.0, "n": 0}
        groups = [{"label": t, **by_grp.get(t, zero)} for t in ladder + extra]
        return {
            "by": by, "groups": groups,
            "total_usd": round(sum(g["pnl_usd"] for g in groups), 2),
            "total_vega_usd": round(sum(g["vega_usd"] for g in groups), 2),
        }
    else:
        raise HTTPException(400, f"unknown pivot '{by}' (expected {[*_PNL_PIVOT_COLS, 'trade']})")
    total = round(sum(g["pnl_usd"] for g in groups), 2)
    return {"by": by, "groups": groups, "total_usd": total}


@router.get("/var")
async def value_at_risk(db: DbDep) -> dict[str, Any]:
    """Historical 1-day Value-at-Risk (R11 G-risk).

    VaR 95 / 99 + ES 99 from the empirical distribution of daily ``net_liq``
    changes over the last ~504 sessions. Values are losses (negative USD).
    Fields ``None`` when < 5 days of history. The factor decomposition
    (skew/level/curvature) + per-position marginal-VaR remain a separate G-risk
    PR (they need greeks × shock attribution, not just the net-liq series).
    """
    # Portfolio settings (editable): VaR lookback window + max day-gap.
    pf = {name: float(val) for name, val in (await db.execute(
        select(AppConfigScalar.name, AppConfigScalar.value)
        .where(AppConfigScalar.namespace == "portfolio")
    )).all()}
    lookback = int(pf.get("var_lookback_days", 504))
    max_gap = int(pf.get("var_max_gap_days", _VAR_MAX_GAP_DAYS))
    nl_sql = text("""
        WITH daily AS (
          SELECT DISTINCT ON (date_trunc('day', timestamp))
                 date_trunc('day', timestamp) AS day, net_liq_usd
            FROM account_history
           WHERE timestamp >= NOW() - make_interval(days => :lookback) AND net_liq_usd IS NOT NULL
           ORDER BY date_trunc('day', timestamp), timestamp DESC
        )
        SELECT day, net_liq_usd FROM daily ORDER BY day
    """)
    rows = [(r[0], float(r[1])) for r in (await db.execute(nl_sql, {"lookback": lookback})).all()]
    # Only consecutive trading-day samples form a genuine 1-day P&L delta. A
    # multi-day hole (system downtime, capital deposit/withdrawal) is NOT a daily
    # loss — including it injects a huge phantom tail that dominates VaR/ES and
    # the histogram. Skip any step spanning more than the configured gap.
    deltas = [
        rows[i][1] - rows[i - 1][1]
        for i in range(1, len(rows))
        if (rows[i][0] - rows[i - 1][0]).days <= max_gap
    ]
    stats = _var_stats(deltas)
    mean_daily = sum(deltas) / len(deltas) if deltas else None
    return {
        "computed_at": datetime.now(UTC).isoformat(),
        "method": "historical",
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
    """Derived greek caps from the stress-loss budget (greek-limits-spec §2/§6/§8).

    Caps are *computed, not configured*: ``L* = ALPHA·nav_base`` is projected
    onto delta/vega/gamma/cross by inverting each axis' shock. ``nav_base`` is
    the slow anchor (0.9·high-water-mark ∨ EWMA-20d of the daily net-liq series)
    so a drawdown does not procyclically tighten every cap at once. The live NAV
    is returned for display only. ``regime_mult`` (§8) scales the caps down as the
    prevailing vol rises above its recent typical level. Fields are 0 until
    ~enough net-liq history + a spot exist.
    """
    nl_sql = text("""
        WITH daily AS (
          SELECT DISTINCT ON (date_trunc('day', timestamp))
                 date_trunc('day', timestamp) AS day, net_liq_usd
            FROM account_history
           WHERE timestamp >= NOW() - INTERVAL '504 days' AND net_liq_usd IS NOT NULL
           ORDER BY date_trunc('day', timestamp), timestamp DESC
        )
        SELECT net_liq_usd FROM daily ORDER BY day
    """)
    nav_series = [float(r[0]) for r in (await db.execute(nl_sql)).all()]
    spot = (await db.execute(
        select(VolSurface.spot).where(VolSurface.underlying == "EURUSD")
        .order_by(desc(VolSurface.timestamp)).limit(1)
    )).scalar_one_or_none()

    # Live policy from the Risk settings panel (config_scalar 'greek_limits'),
    # falling back to the code defaults for any key not set in the DB.
    rows = (await db.execute(
        select(AppConfigScalar.name, AppConfigScalar.value)
        .where(AppConfigScalar.namespace == "greek_limits")
    )).all()
    params = {name: float(val) for name, val in rows if name in gl.CONFIG_DEFAULTS}

    nav_b = gl.nav_base(
        nav_series,
        hwm_floor=params.get("nav_hwm_floor", gl.CONFIG_DEFAULTS["nav_hwm_floor"]),
        halflife=params.get("nav_halflife_days", gl.CONFIG_DEFAULTS["nav_halflife_days"]),
    ) or 0.0
    spot_f = float(spot) if spot is not None else 0.0
    # §8 — regime scaling: tighten caps when current vol is elevated vs its recent
    # typical level. regime_mult = clamp(current / median(last 90d), 1, 3), both
    # read from regime_snapshot.vol_level_pct so the ratio is unit-independent (no
    # calm-baseline constant to guess). Falls back to 1.0 when history is thin.
    vol_levels = [
        float(r[0]) for r in (await db.execute(
            select(RegimeSnapshot.vol_level_pct)
            .where(
                RegimeSnapshot.vol_level_pct.is_not(None),
                RegimeSnapshot.timestamp >= datetime.now(UTC) - timedelta(days=90),
            )
            .order_by(RegimeSnapshot.timestamp)
        )).all()
    ]
    regime = 1.0
    if len(vol_levels) >= 10:
        baseline = statistics.median(vol_levels)
        if baseline > 0:
            regime = max(1.0, min(3.0, vol_levels[-1] / baseline))
    caps = gl.compute_caps(nav_b, spot_f, regime, params=params)
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

    Sharpe + drawdown from the daily net-liq curve (``account_history``),
    hit-rate + cumulative realized from closed booked positions, cumulative
    unrealized from the live book. Fields are ``None`` when the underlying
    series is too short / empty (read-only public deployment at boot).
    """
    nl_sql = text("""
        WITH daily AS (
          SELECT DISTINCT ON (date_trunc('day', timestamp))
                 date_trunc('day', timestamp) AS day, net_liq_usd
            FROM account_history
           WHERE timestamp >= NOW() - INTERVAL '365 days' AND net_liq_usd IS NOT NULL
           ORDER BY date_trunc('day', timestamp), timestamp DESC
        )
        SELECT net_liq_usd FROM daily ORDER BY day
    """)
    nl = [float(r[0]) for r in (await db.execute(nl_sql)).all()]
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
        "computed_at": datetime.now(UTC).isoformat(),
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

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

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db_session
from core.pricing.bs import bs_delta, bs_gamma, bs_price, bs_theta, bs_vega
from persistence.models import AccountHistory, OpenPosition, OpenPositionHistory  # noqa: F401
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

    return {
        "latest": _serialize_snap(latest),
        "prev_24h": _serialize_snap(prev),
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


# Spot bins (bp move from current) and IV bins (vol points absolute shift).
# Defaults from risk_dashboard_spec.md § F. Spot range ~1σ–2σ daily for FX,
# vol range capturing typical regime shifts.
_STRESS_SPOT_BPS = [-200, -100, -50, 0, 50, 100, 200]
_STRESS_VOL_VPS = [3, 1, 0, -1, -3]   # rows top→bottom (+3 vp on top)


@router.get("/stress-grid")
async def stress_grid(db: DbDep) -> dict[str, Any]:
    """5×7 spot × IV stress matrix. Each cell = ``NPV(scenario) - NPV(now)``.

    Full revaluation per scenario via Black-Scholes for options, linear for
    futures. Baseline = current ``market_price`` for futures, BS at current
    ``iv`` for options. Matches spec ``risk_dashboard_spec.md § F``.
    """
    open_positions = (await db.execute(select(OpenPosition))).scalars().all()
    if not open_positions:
        return {
            "current_spot": None,
            "spot_bins_bps": _STRESS_SPOT_BPS,
            "vol_bins_vps": _STRESS_VOL_VPS,
            "grid": [],
            "n_positions": 0,
        }

    # Spot proxy : take any FUTURE marketPrice (most reliable). Falls back to
    # the underlying spot derived from option moneyness if no future is held.
    current_spot: float | None = None
    for p in open_positions:
        spec = parse_local_symbol(p.structure)
        if spec and spec.instrument_type == "FUTURE" and p.market_price:
            current_spot = float(p.market_price)
            break
    if current_spot is None:
        # Fallback : an option's underlying must have been priced upstream.
        for p in open_positions:
            if p.market_price and p.iv:
                # rough proxy : option mid-strike ≈ spot for ATM positions.
                spec = parse_local_symbol(p.structure)
                if spec and spec.strike:
                    current_spot = float(spec.strike)
                    break
    if current_spot is None:
        return {
            "current_spot": None,
            "spot_bins_bps": _STRESS_SPOT_BPS,
            "vol_bins_vps": _STRESS_VOL_VPS,
            "grid": [],
            "n_positions": len(open_positions),
        }

    today = datetime.now(UTC).date()

    # Pre-compute baseline NPV per position once.
    baselines: list[dict[str, Any]] = []
    for p in open_positions:
        spec = parse_local_symbol(p.structure)
        if spec is None:
            continue
        qty_signed = float(p.quantity) * (1.0 if p.side == "BUY" else -1.0)
        mult = spec.multiplier
        if spec.instrument_type == "FUTURE":
            baselines.append({
                "type": "FUTURE",
                "qty_signed": qty_signed, "mult": mult,
                "npv_base": qty_signed * mult * current_spot,
            })
        elif spec.option_type and spec.strike and p.expiry and p.iv:
            T = max(0.001, (p.expiry - today).days / 365.0)
            iv_dec = float(p.iv)
            right = "C" if spec.option_type == "CALL" else "P"
            base_price = bs_price(current_spot, spec.strike, T, iv_dec, right)
            baselines.append({
                "type": "OPTION",
                "qty_signed": qty_signed, "mult": mult,
                "K": float(spec.strike), "T": T, "iv": iv_dec, "right": right,
                "npv_base": qty_signed * mult * base_price,
            })

    grid: list[list[float]] = []
    for dvol_vp in _STRESS_VOL_VPS:
        dsigma = dvol_vp / 100.0
        row: list[float] = []
        for dspot_bp in _STRESS_SPOT_BPS:
            new_spot = current_spot * (1.0 + dspot_bp / 10000.0)
            total_pnl = 0.0
            for b in baselines:
                if b["type"] == "FUTURE":
                    npv_new = b["qty_signed"] * b["mult"] * new_spot
                else:
                    new_iv = b["iv"] + dsigma
                    if new_iv <= 0:
                        continue
                    new_price = bs_price(
                        new_spot, b["K"], b["T"], new_iv, b["right"],
                    )
                    npv_new = b["qty_signed"] * b["mult"] * new_price
                total_pnl += npv_new - b["npv_base"]
            row.append(round(total_pnl, 2))
        grid.append(row)

    return {
        "current_spot": round(current_spot, 5),
        "spot_bins_bps": _STRESS_SPOT_BPS,
        "vol_bins_vps": _STRESS_VOL_VPS,
        "grid": grid,
        "n_positions": len(baselines),
    }


# Spot ladder bins (bp move from current). Spec ``risk_dashboard_spec.md § H``.
_LADDER_SPOT_BPS = [-400, -200, 0, 200, 400]


@router.get("/greeks-ladder")
async def greeks_ladder(db: DbDep) -> dict[str, Any]:
    """Per-spot-bucket greeks ladder. For each ΔSpot in {-400, -200, 0, +200, +400} bp :
    full revaluation of the book, then sum Δ / Γ / Vega and the resulting
    P&L vs current. ``hedge_delta_usd`` = ``-delta_usd`` (qty of $ Δ to
    short/long via futures to be delta-neutral at that spot).
    """
    open_positions = (await db.execute(select(OpenPosition))).scalars().all()
    if not open_positions:
        return {
            "current_spot": None,
            "spot_bins_bps": _LADDER_SPOT_BPS,
            "rows": [],
        }

    current_spot: float | None = None
    for p in open_positions:
        spec = parse_local_symbol(p.structure)
        if spec and spec.instrument_type == "FUTURE" and p.market_price:
            current_spot = float(p.market_price)
            break
    if current_spot is None:
        return {
            "current_spot": None,
            "spot_bins_bps": _LADDER_SPOT_BPS,
            "rows": [],
        }

    today = datetime.now(UTC).date()

    # Pre-extract per-position contract spec + baseline NPV.
    positions_resolved: list[dict[str, Any]] = []
    for p in open_positions:
        spec = parse_local_symbol(p.structure)
        if spec is None:
            continue
        qty_signed = float(p.quantity) * (1.0 if p.side == "BUY" else -1.0)
        mult = spec.multiplier
        if spec.instrument_type == "FUTURE":
            positions_resolved.append({
                "type": "FUTURE",
                "qty_signed": qty_signed, "mult": mult,
                "npv_base": qty_signed * mult * current_spot,
            })
        elif spec.option_type and spec.strike and p.expiry and p.iv:
            T = max(0.001, (p.expiry - today).days / 365.0)
            iv_dec = float(p.iv)
            right = "C" if spec.option_type == "CALL" else "P"
            base_price = bs_price(current_spot, spec.strike, T, iv_dec, right)
            positions_resolved.append({
                "type": "OPTION",
                "qty_signed": qty_signed, "mult": mult,
                "K": float(spec.strike), "T": T, "iv": iv_dec, "right": right,
                "npv_base": qty_signed * mult * base_price,
            })

    rows: list[dict[str, Any]] = []
    for dspot_bp in _LADDER_SPOT_BPS:
        new_spot = current_spot * (1.0 + dspot_bp / 10000.0)
        total_pnl = 0.0
        total_delta = 0.0
        total_gamma = 0.0
        total_vega = 0.0
        for pos in positions_resolved:
            if pos["type"] == "FUTURE":
                npv_new = pos["qty_signed"] * pos["mult"] * new_spot
                total_delta += pos["qty_signed"] * pos["mult"]
                # Γ / vega = 0 for futures.
            else:
                K, T, iv_dec, right = pos["K"], pos["T"], pos["iv"], pos["right"]
                new_price = bs_price(new_spot, K, T, iv_dec, right)
                npv_new = pos["qty_signed"] * pos["mult"] * new_price
                total_delta += pos["qty_signed"] * pos["mult"] * bs_delta(
                    new_spot, K, T, iv_dec, right,
                )
                total_gamma += (
                    pos["qty_signed"] * pos["mult"]
                    * bs_gamma(new_spot, K, T, iv_dec) * 1e-4  # $/pip
                )
                total_vega += (
                    pos["qty_signed"] * pos["mult"]
                    * bs_vega(new_spot, K, T, iv_dec) * 0.01  # $/volpt
                )
            total_pnl += npv_new - pos["npv_base"]
        rows.append({
            "dspot_bps": dspot_bp,
            "spot": round(new_spot, 5),
            "pnl_usd": round(total_pnl, 2),
            "delta_usd": round(total_delta, 2),
            "gamma_usd_per_pip": round(total_gamma, 2),
            "vega_usd_per_volpt": round(total_vega, 2),
            "hedge_delta_usd": -round(total_delta, 2),
        })

    return {
        "current_spot": round(current_spot, 5),
        "spot_bins_bps": _LADDER_SPOT_BPS,
        "rows": rows,
        "n_positions": len(positions_resolved),
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

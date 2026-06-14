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
from core.pricing.bs import bs_delta, bs_gamma, bs_price, bs_vega
from persistence.models import AccountSnap, Position, PositionSnapshot  # noqa: F401
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


def _serialize_snap(s: AccountSnap | None) -> dict[str, Any] | None:
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
        select(AccountSnap).order_by(desc(AccountSnap.timestamp)).limit(1)
    )).scalar_one_or_none()

    prev: AccountSnap | None = None
    if latest is not None:
        cutoff = latest.timestamp - timedelta(hours=24)
        prev = (await db.execute(
            select(AccountSnap).where(AccountSnap.timestamp <= cutoff)
            .order_by(desc(AccountSnap.timestamp)).limit(1)
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
        select(AccountSnap).order_by(desc(AccountSnap.timestamp)).limit(1)
    )).scalar_one_or_none()
    prev: AccountSnap | None = None
    if latest is not None:
        cutoff = latest.timestamp - timedelta(hours=24)
        prev = (await db.execute(
            select(AccountSnap).where(AccountSnap.timestamp <= cutoff)
            .order_by(desc(AccountSnap.timestamp)).limit(1)
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
          MAX(updated_at)                       AS last_ts
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
    open_positions = (await db.execute(select(Position))).scalars().all()
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
    open_positions = (await db.execute(select(Position))).scalars().all()
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

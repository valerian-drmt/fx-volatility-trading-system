"""Materialised portfolio rollups: writers and readers (#368, hot/cold T4).

Two slow results used to be recomputed inside every HTTP request:

* the **daily net-liq series** (``DISTINCT ON (date_trunc('day', timestamp))``
  over all of ``account_history``) behind ``/portfolio/stats``,
  ``/daily-pnl``, ``/header``, the ``/var`` fallback and the greek caps;
* the **P&L attribution** (two LATERAL probes + the Taylor decomposition)
  behind ``/portfolio/pnl-attribution``.

The analytics engine now calls :func:`refresh_portfolio_daily` and
:func:`refresh_attribution` every few minutes; the endpoints call the
``read_*`` functions and get one row. The api keeps an inline fallback
(``compute_attribution`` / a bounded refresh) for a stack where the engine
has not run yet, so a panel never blanks; prod reads rows.

Postgres gets the index-friendly SQL (one backward index step per day, one
per open position); the SQLite path used by the unit tests folds the same
numbers in Python.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Insert, desc, insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.contracts import is_linear, parse_local_symbol
from core.risk.attribution import attrib_tenor_bucket, decompose_row, iv_fraction_to_pts
from core.units import PIP_SIZE
from persistence.models import AccountHistory, PnlAttributionLatest, PortfolioDaily

logger = logging.getLogger(__name__)

# Lookback windows the engine keeps warm. 24 h is what the desk shows; the
# others serve the selector without a request-path compute.
DEFAULT_LOOKBACKS_H: tuple[int, ...] = (1, 4, 24, 72, 168)
# A stored attribution older than this is treated as missing by the api.
ATTRIBUTION_MAX_AGE_S: float = 15 * 60.0
# How far back the first (empty-table) daily rollup looks.
BACKFILL_DAYS: int = 400
# Rolling refresh window once the table is seeded: today, yesterday and one
# more day so a late sample or a weekend edge is picked up.
ROLLING_DAYS: int = 3


# ───────────────────────────────────────────── portfolio_daily : writer ────

def _dec(v: Any) -> Decimal | None:
    return None if v is None else Decimal(str(v))


async def _daily_samples_pg(db: AsyncSession, start: date, end: date) -> list[dict[str, Any]]:
    """Per UTC day in [start, end]: first/last net-liq sample and the count.
    One LATERAL pair per day on ``ix_account_history_ts``, never a scan."""
    rows = (await db.execute(text("""
        SELECT d.day::date AS day,
               o.net_liq_usd AS net_liq_open, o.timestamp AS first_sample_at,
               c.net_liq_usd AS net_liq_close, c.timestamp AS last_sample_at,
               (SELECT COUNT(*) FROM account_history a
                 WHERE a.timestamp >= d.day AND a.timestamp < d.day + INTERVAL '1 day'
                   AND a.net_liq_usd IS NOT NULL) AS n_samples
          FROM generate_series(CAST(:start AS date), CAST(:end AS date), INTERVAL '1 day') AS d(day)
          LEFT JOIN LATERAL (
                 SELECT net_liq_usd, timestamp FROM account_history
                  WHERE timestamp >= d.day AND timestamp < d.day + INTERVAL '1 day'
                    AND net_liq_usd IS NOT NULL
                  ORDER BY timestamp ASC LIMIT 1) o ON TRUE
          LEFT JOIN LATERAL (
                 SELECT net_liq_usd, timestamp FROM account_history
                  WHERE timestamp >= d.day AND timestamp < d.day + INTERVAL '1 day'
                    AND net_liq_usd IS NOT NULL
                  ORDER BY timestamp DESC LIMIT 1) c ON TRUE
         ORDER BY d.day
    """), {"start": start, "end": end})).all()
    return [
        {"day": r.day, "net_liq_open": r.net_liq_open, "first_sample_at": r.first_sample_at,
         "net_liq_close": r.net_liq_close, "last_sample_at": r.last_sample_at, "n_samples": int(r.n_samples or 0)}
        for r in rows if r.net_liq_close is not None
    ]


async def _daily_samples_portable(db: AsyncSession, start: date, end: date) -> list[dict[str, Any]]:
    """SQLite (tests): fold the window's rows in Python. Same output shape."""
    lo = datetime.combine(start, datetime.min.time(), tzinfo=UTC)
    hi = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    rows = (await db.execute(
        select(AccountHistory.timestamp, AccountHistory.net_liq_usd)
        .where(AccountHistory.timestamp >= lo, AccountHistory.timestamp < hi,
               AccountHistory.net_liq_usd.is_not(None))
        .order_by(AccountHistory.timestamp)
    )).all()
    by_day: dict[date, dict[str, Any]] = {}
    for ts, nl in rows:
        ts = ts if ts.tzinfo else ts.replace(tzinfo=UTC)
        d = by_day.setdefault(ts.date(), {"day": ts.date(), "net_liq_open": nl, "first_sample_at": ts,
                                          "net_liq_close": nl, "last_sample_at": ts, "n_samples": 0})
        d["net_liq_close"], d["last_sample_at"] = nl, ts
        d["n_samples"] += 1
    return [by_day[d] for d in sorted(by_day)]


async def _realized_by_day(db: AsyncSession, start: date, end: date) -> dict[date, tuple[float, int]]:
    """Realised P&L of GENUINE closes per day (``net_pnl_usd`` set by the
    finaliser; reconciliation auto-closes carry NULL and are not trades)."""
    lo = datetime.combine(start, datetime.min.time(), tzinfo=UTC)
    hi = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    rows = (await db.execute(text("""
        SELECT closed_at, net_pnl_usd FROM booked_position
         WHERE state = 'closed' AND closed_at IS NOT NULL AND net_pnl_usd IS NOT NULL
           AND closed_at >= :lo AND closed_at < :hi
    """), {"lo": lo, "hi": hi})).all()
    out: dict[date, tuple[float, int]] = {}
    for closed_at, pnl in rows:
        if isinstance(closed_at, str):
            closed_at = datetime.fromisoformat(closed_at)
        d = (closed_at if closed_at.tzinfo else closed_at.replace(tzinfo=UTC)).date()
        acc, n = out.get(d, (0.0, 0))
        out[d] = (acc + float(pnl), n + 1)
    return out


def _upsert_daily(dialect: str, rows: list[dict[str, Any]]) -> Insert:
    cols = {k: getattr(PortfolioDaily, k) for k in rows[0] if k != "day"}
    if dialect == "postgresql":
        stmt = pg_insert(PortfolioDaily).values(rows)
        return stmt.on_conflict_do_update(index_elements=["day"], set_={k: stmt.excluded[k] for k in cols})
    if dialect == "sqlite":
        stmt_s = sqlite_insert(PortfolioDaily).values(rows)
        return stmt_s.on_conflict_do_update(index_elements=["day"], set_={k: stmt_s.excluded[k] for k in cols})
    return insert(PortfolioDaily).values(rows)


async def refresh_portfolio_daily(
    db: AsyncSession, *, days: int | None = None, now: datetime | None = None,
) -> dict[str, Any]:
    """Upsert one ``portfolio_daily`` row per UTC day that has account samples.

    ``days=None`` picks :data:`BACKFILL_DAYS` when the table is empty and
    :data:`ROLLING_DAYS` otherwise. The MTM of the first refreshed day is
    taken against the stored close of the day before it, so a rolling
    refresh never breaks the chain. Commits."""
    now = now or datetime.now(UTC)
    today = now.date()
    if days is None:
        has_rows = (await db.execute(select(PortfolioDaily.id).limit(1))).scalar_one_or_none() is not None
        days = ROLLING_DAYS if has_rows else BACKFILL_DAYS
    start = today - timedelta(days=days - 1)
    dialect = db.bind.dialect.name if db.bind is not None else "sqlite"
    samples = await (_daily_samples_pg(db, start, today) if dialect == "postgresql"
                     else _daily_samples_portable(db, start, today))
    if not samples:
        return {"days": days, "rows": 0, "computed_at": now.isoformat()}
    realized = await _realized_by_day(db, start, today)
    prev_close: Decimal | None = (await db.execute(
        select(PortfolioDaily.net_liq_close).where(PortfolioDaily.day < start)
        .order_by(desc(PortfolioDaily.day)).limit(1)
    )).scalar_one_or_none()
    rows: list[dict[str, Any]] = []
    for s in samples:
        close = _dec(s["net_liq_close"])
        mtm = (close - prev_close) if (close is not None and prev_close is not None) else None
        r_usd, n_closed = realized.get(s["day"], (0.0, 0))
        rows.append({
            "day": s["day"],
            "net_liq_open": _dec(s["net_liq_open"]),
            "net_liq_close": close,
            "mtm_usd": mtm,
            "realized_usd": Decimal(str(round(r_usd, 2))),
            "n_closed": n_closed,
            "n_samples": int(s["n_samples"]),
            "first_sample_at": s["first_sample_at"],
            "last_sample_at": s["last_sample_at"],
            "computed_at": now,
        })
        prev_close = close if close is not None else prev_close
    await db.execute(_upsert_daily(dialect, rows))
    await db.commit()
    return {"days": days, "rows": len(rows), "computed_at": now.isoformat()}


# ───────────────────────────────────────────── account : latest row ───────

async def latest_account_row(db: AsyncSession) -> dict[str, Any] | None:
    """ONE indexed read (``ix_account_history_ts``, ``ORDER BY timestamp DESC
    LIMIT 1``) of the latest account snapshot, serialised like the
    ``account`` channel. The header's fallback when the Redis cache is cold;
    the only ``account_history`` access left on that request path."""
    from persistence.rows import account_snapshot_row

    row = (await db.execute(
        select(AccountHistory).order_by(desc(AccountHistory.timestamp)).limit(1)
    )).scalar_one_or_none()
    return account_snapshot_row(row)


# ───────────────────────────────────────────── portfolio_daily : readers ───

async def read_portfolio_daily(db: AsyncSession, *, days: int, now: datetime | None = None) -> list[PortfolioDaily]:
    """The last ``days`` daily rows, oldest first. ``now`` pins the clock (tests,
    replays); like ``refresh_portfolio_daily``, it defaults to the wall clock."""
    start = (now or datetime.now(UTC)).date() - timedelta(days=days - 1)
    return list((await db.execute(
        select(PortfolioDaily).where(PortfolioDaily.day >= start).order_by(PortfolioDaily.day)
    )).scalars().all())


async def read_nav_series(
    db: AsyncSession, *, days: int, now: datetime | None = None,
) -> tuple[list[tuple[date, float]], datetime | None]:
    """``(day, net_liq_close)`` pairs oldest first + the rollup's computed_at.
    Replaces the per-request ``DISTINCT ON`` daily close everywhere."""
    rows = await read_portfolio_daily(db, days=days, now=now)
    series = [(r.day, float(r.net_liq_close)) for r in rows if r.net_liq_close is not None]
    computed_at = max((r.computed_at for r in rows), default=None)
    return series, computed_at


async def read_close_before(db: AsyncSession, cutoff: datetime) -> PortfolioDaily | None:
    """The latest daily row whose day ended at or before ``cutoff``'s day
    (yesterday's close for a 24 h delta)."""
    return (await db.execute(
        select(PortfolioDaily).where(PortfolioDaily.day < cutoff.date())
        .order_by(desc(PortfolioDaily.day)).limit(1)
    )).scalar_one_or_none()


# ───────────────────────────────────────────── attribution : compute ──────

_IB_SQL = text("""
    SELECT p.id, p.structure, p.product_label, p.side, p.tenor, p.expiry,
           p.trade_id, p.contract_id, p.nominal_eur,
           ts.structure_type AS structure_type,
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
           t1.delta_usd       AS delta_then,
           t1.gamma_usd       AS gamma_then,
           t1.vega_usd        AS vega_then,
           t1.theta_usd       AS theta_then,
           t1.nominal_eur     AS nominal_then,
           t1.timestamp       AS t_then,
           older.timestamp    AS t_older
      FROM open_position p
      LEFT JOIN LATERAL (
             SELECT h.timestamp, h.market_price, h.current_pnl_usd, h.iv,
                    h.delta_usd, h.gamma_usd, h.vega_usd, h.theta_usd, h.nominal_eur
               FROM open_position_history h
              WHERE h.position_id = p.id
                AND h.timestamp <= :cutoff
                AND h.timestamp >= :floor
              ORDER BY h.timestamp DESC
              LIMIT 1
           ) t1 ON TRUE
      LEFT JOIN LATERAL (
             SELECT h.timestamp
               FROM open_position_history h
              WHERE h.position_id = p.id
                AND h.timestamp < :floor
              ORDER BY h.timestamp DESC
              LIMIT 1
           ) older ON TRUE
      LEFT JOIN trade_structure ts ON ts.id = p.trade_id
""")

# The window-start snapshot must sit within this distance BEFORE the cutoff.
# The risk engine writes a row at least every 15 min, so a snapshot older than
# this means history has a hole there. On 2026-11-12 the "24 h" attribution
# anchored on a price 46 h old (the engine had been disconnected from IB for a
# day) and every delta term came out about 2.3 times too large (#467).
THEN_TOLERANCE = timedelta(minutes=45)

STATUS_OK = "ok"
STATUS_OPENED_IN_WINDOW = "opened_in_window"        # no history before the cutoff at all
STATUS_INSUFFICIENT_HISTORY = "insufficient_history"  # history exists, but not near the cutoff

async def underlying_forward(
    db: AsyncSession, cutoff: datetime, floor: datetime | None = None,
) -> tuple[float | None, float | None]:
    """Book-level underlying forward (the 6E future's market price) now and at
    the latest snapshot ≤ ``cutoff``. The t-1 probe is anchored on the OPEN 6E
    positions' ids and walks the ``(position_id, timestamp)`` index one step
    per future; skipped outright when no future is open (prod 2026-11-05)."""
    fwd_row = (await db.execute(text(
        "SELECT market_price FROM open_position "
        "WHERE structure LIKE '6E%' AND market_price IS NOT NULL ORDER BY id DESC LIMIT 1"
    ))).first()
    forward = float(fwd_row[0]) if fwd_row and fwd_row[0] is not None else None
    if forward is None:
        return None, None
    fwd_then_row = (await db.execute(text(
        "SELECT h.market_price "
        "  FROM open_position op "
        "  JOIN LATERAL ("
        "        SELECT h.market_price, h.timestamp "
        "          FROM open_position_history h "
        "         WHERE h.position_id = op.id "
        "           AND h.timestamp <= :cutoff "
        "           AND h.timestamp >= :floor "
        "           AND h.market_price IS NOT NULL "
        "         ORDER BY h.timestamp DESC "
        "         LIMIT 1"
        "       ) h ON TRUE "
        " WHERE op.structure LIKE '6E%' "
        " ORDER BY h.timestamp DESC LIMIT 1"
    ), {"cutoff": cutoff, "floor": floor or (cutoff - THEN_TOLERANCE)})).first()
    forward_then = float(fwd_then_row[0]) if fwd_then_row and fwd_then_row[0] is not None else None
    return forward, forward_then


def _wing(local_symbol: str | None, forward: float | None) -> str:
    """Wing bucket by strike vs forward: put wing (below), body (ATM ±50 bp),
    call wing (above). Futures / unparseable legs fall back."""
    spec = parse_local_symbol(local_symbol)
    if spec is None:
        return "other"
    if spec.instrument_type == "FUTURE" or spec.strike is None:
        return "Future / delta-1"
    if forward is None or forward <= 0:
        return "other"
    moneyness = spec.strike / forward - 1.0
    if abs(moneyness) <= 0.005:
        return "Body (ATM)"
    return "Put wing" if moneyness < 0 else "Call wing"


def _wing_detail(local_symbol: str | None, forward: float | None) -> str | None:
    """The wing with the option's own right when they differ: a call struck
    below the forward sits on the put wing of the smile and is an ITM call,
    which the bare label hid ("Put wing" on a call)."""
    wing = _wing(local_symbol, forward)
    spec = parse_local_symbol(local_symbol)
    if spec is None or spec.option_type is None:
        return wing
    if wing == "Put wing" and spec.option_type == "CALL":
        return "Put wing (ITM call)"
    if wing == "Call wing" and spec.option_type == "PUT":
        return "Call wing (ITM put)"
    return wing


def _f(v: Any) -> float | None:
    return float(v) if v is not None else None


def _dt(v: Any) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, str):
        v = datetime.fromisoformat(v)
    return v if v.tzinfo else v.replace(tzinfo=UTC)


async def compute_attribution(
    db: AsyncSession,
    *,
    now: datetime,
    lookback_hours: int,
    spot_fallback: tuple[float | None, float | None] | None = None,
) -> dict[str, Any]:
    """The per-position Taylor decomposition over the window (both sources).

    Returns ``{"per_position": [...], "meta": {...}}``; ``meta`` carries
    ``lookback_hours``, ``computed_at``, ``window_start``, ``forward_source``.
    ``spot_fallback`` is the market-data cache's (now, then) spot, used for
    whichever side of dS no open 6E future provides."""
    cutoff = now - timedelta(hours=lookback_hours)
    floor = cutoff - THEN_TOLERANCE
    ib_rows = (await db.execute(_IB_SQL, {"cutoff": cutoff, "floor": floor})).all()
    forward, forward_then = await underlying_forward(db, cutoff, floor)
    forward_source = "future_mark"
    if forward is None or forward_then is None:
        fb_now, fb_then = spot_fallback or (None, None)
        if forward is None and fb_now is not None:
            forward = fb_now
            forward_source = "spot_cache"
        if forward_then is None and fb_then is not None:
            forward_then = fb_then
            forward_source = "spot_cache"
        if forward is None or forward_then is None:
            forward_source = "none"
    today = now.date()
    per_position: list[dict[str, Any]] = []
    for r in ib_rows:
        spec = parse_local_symbol(r.structure)
        linear = (
            (spec is not None and is_linear(spec.instrument_type))
            or (r.product_label or "").lower().startswith("future")
        )
        t_then = _dt(r.t_then)
        if t_then is None:
            status = STATUS_INSUFFICIENT_HISTORY if _dt(getattr(r, "t_older", None)) is not None else STATUS_OPENED_IN_WINDOW
        else:
            status = STATUS_OK

        # ONE P&L source at both ends of the window: the contract's own IB
        # mark. ``current_pnl_usd`` is IB's unrealised P&L when IB reports it
        # and our model's otherwise, so a window that started on one and
        # ended on the other showed the gap between them as a gain (+1,610 on
        # a future on 2026-11-12: spot-to-future basis). The marks give the
        # same answer whatever the engine was connected to. Only when the
        # quantity did not change, and both marks exist.
        mark_now, mark_then = _f(r.spot_now), _f(r.spot_then)
        nominal, nominal_then = _f(r.nominal_eur), _f(getattr(r, "nominal_then", None))
        sign = 1.0 if (r.side or "").upper() == "BUY" else -1.0
        same_size = nominal is not None and (nominal_then is None or abs(nominal - nominal_then) < 1e-6)
        pnl_basis = "pnl_column"
        pnl_now_u, pnl_then_u = _f(r.pnl_now), _f(r.pnl_then)
        mark_move: float | None = None
        if (
            t_then is not None and mark_now is not None and mark_then is not None
            and nominal is not None and same_size
        ):
            mark_move = sign * nominal * (mark_now - mark_then)
            pnl_now_u, pnl_then_u = mark_move, 0.0
            pnl_basis = "ib_mark"

        decomp: dict[str, float | None]
        if linear:
            # A delta-one line's P&L IS its delta term: quantity x multiplier
            # x the move of its own price. Nothing else, no residual.
            if mark_move is not None:
                move = round(mark_move, 2)
                decomp = {
                    "actual_pnl_usd": move, "delta_pnl_usd": move, "gamma_pnl_usd": 0.0,
                    "vega_pnl_usd": 0.0, "theta_pnl_usd": 0.0, "residual_usd": 0.0,
                }
            else:
                decomp = dict.fromkeys(
                    ("actual_pnl_usd", "delta_pnl_usd", "gamma_pnl_usd", "vega_pnl_usd", "theta_pnl_usd", "residual_usd"),
                    None,
                )
                if status == STATUS_OK:
                    status = STATUS_INSUFFICIENT_HISTORY   # a snapshot without a mark cannot price the move
        else:
            decomp = decompose_row(
                now=now, pnl_now=pnl_now_u, pnl_then=pnl_then_u,
                spot_now=forward, spot_then=forward_then,
                iv_now=iv_fraction_to_pts(r.iv_now), iv_then=iv_fraction_to_pts(r.iv_then),
                delta=(float(r.delta_now) / forward) if (r.delta_now is not None and forward) else None,
                gamma=_f(r.gamma_now), vega=_f(r.vega_now), theta=_f(r.theta_now), t_then=t_then,
                delta_then=(float(r.delta_then) / forward_then) if (r.delta_then is not None and forward_then) else None,
                gamma_then=_f(r.gamma_then), vega_then=_f(r.vega_then), theta_then=_f(r.theta_then),
                gamma_scale=1.0 / PIP_SIZE,
            )
        expiry = r.expiry if not isinstance(r.expiry, str) else date.fromisoformat(r.expiry)
        per_position.append({
            "id": int(r.id), "source": "ib_live",
            "structure": r.structure, "product_label": r.product_label,
            "side": r.side, "tenor": r.tenor,
            "attrib_tenor": attrib_tenor_bucket(expiry, today),
            "trade_id": int(r.trade_id) if r.trade_id is not None else None,
            "contract_id": int(r.contract_id) if r.contract_id is not None else None,
            "nominal_eur": _f(r.nominal_eur),
            "iv": _f(r.iv_now),
            "structure_type": r.structure_type,
            "wing": _wing(r.structure, forward),
            "wing_detail": _wing_detail(r.structure, forward),
            "attrib_status": status,
            "pnl_basis": pnl_basis,
            "t_then": t_then.isoformat() if t_then is not None else None,
            **decomp,
        })
    # No ``booked_position`` rows any more (#461, #467): they were a second,
    # trade-level copy of positions the lines above already carry, stored and
    # then hidden by a filter. The line set is the one list.
    meta = {
        "lookback_hours": lookback_hours,
        "computed_at": now.isoformat(),
        "window_start": cutoff.isoformat(),
        "forward_source": forward_source,
        "then_tolerance_minutes": int(THEN_TOLERANCE.total_seconds() // 60),
    }
    return {"per_position": per_position, "meta": meta}


# ───────────────────────────────────────────── attribution : store ───────

async def write_attribution_latest(db: AsyncSession, result: dict[str, Any]) -> None:
    """Upsert the (lookback_hours) row. Commits."""
    meta = result["meta"]
    values = {
        "lookback_hours": int(meta["lookback_hours"]),
        "computed_at": datetime.fromisoformat(meta["computed_at"]),
        "window_start": datetime.fromisoformat(meta["window_start"]),
        "forward_source": str(meta["forward_source"])[:20],
        "n_positions": len(result["per_position"]),
        "per_position": result["per_position"],
    }
    dialect = db.bind.dialect.name if db.bind is not None else "sqlite"
    set_ = {k: v for k, v in values.items() if k != "lookback_hours"}
    if dialect == "postgresql":
        stmt_p = pg_insert(PnlAttributionLatest).values(**values)
        stmt: Any = stmt_p.on_conflict_do_update(index_elements=["lookback_hours"], set_={k: stmt_p.excluded[k] for k in set_})
    else:
        stmt_s = sqlite_insert(PnlAttributionLatest).values(**values)
        stmt = stmt_s.on_conflict_do_update(index_elements=["lookback_hours"], set_={k: stmt_s.excluded[k] for k in set_})
    await db.execute(stmt)
    await db.commit()


async def read_attribution_latest(
    db: AsyncSession, *, lookback_hours: int, max_age_s: float = ATTRIBUTION_MAX_AGE_S,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """The stored decomposition in ``compute_attribution``'s shape, or None
    when there is no row for the window or it is older than ``max_age_s``."""
    row = (await db.execute(
        select(PnlAttributionLatest).where(PnlAttributionLatest.lookback_hours == lookback_hours)
    )).scalar_one_or_none()
    if row is None:
        return None
    now = now or datetime.now(UTC)
    computed_at = row.computed_at if row.computed_at.tzinfo else row.computed_at.replace(tzinfo=UTC)
    if (now - computed_at).total_seconds() > max_age_s:
        return None
    window_start = row.window_start if row.window_start.tzinfo else row.window_start.replace(tzinfo=UTC)
    return {
        "per_position": list(row.per_position or []),
        "meta": {
            "lookback_hours": row.lookback_hours,
            "computed_at": computed_at.isoformat(),
            "window_start": window_start.isoformat(),
            "forward_source": row.forward_source,
        },
    }


async def refresh_attribution(
    db: AsyncSession, *, now: datetime, lookbacks: Sequence[int] = DEFAULT_LOOKBACKS_H,
    spot_fallback_for: Any = None,
) -> dict[str, Any]:
    """Compute + store every lookback. ``spot_fallback_for(cutoff)`` is an
    async callable returning the cache's (now, then) spot for a window
    (``bus.spot_cache.spot_fallbacks`` bound to a Redis client)."""
    n_rows = 0
    for lb in lookbacks:
        fb = None
        if spot_fallback_for is not None:
            try:
                fb = await spot_fallback_for(now - timedelta(hours=lb))
            except Exception:
                fb = None
        result = await compute_attribution(db, now=now, lookback_hours=lb, spot_fallback=fb)
        await write_attribution_latest(db, result)
        n_rows += len(result["per_position"])
    return {"lookbacks": list(lookbacks), "n_rows": n_rows, "computed_at": now.isoformat()}

"""Materialised portfolio rollups (#368, hot/cold T4).

``portfolio_daily`` is folded from ``account_history`` + ``booked_position``
(SQLite path here, same numbers as the Postgres LATERAL path); a rolling
refresh keeps the MTM chain against the stored previous close; the
attribution row round-trips and ages out. ``compute_attribution`` is driven
with a fake session because its LATERAL probes need Postgres.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("pytest_asyncio")

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


async def _seed_account(maker: Any, points: list[tuple[datetime, float]]) -> None:
    from persistence.models import AccountHistory
    async with maker() as db:
        for ts, nl in points:
            db.add(AccountHistory(timestamp=ts, net_liq_usd=Decimal(str(nl)), cash_usd=Decimal("1")))
        await db.commit()


async def _seed_close(maker: Any, *, closed_at: datetime, pnl: float | None, reason: str | None = None) -> None:
    from persistence.models import BookedPosition, TradeStructure
    async with maker() as db:
        s = TradeStructure(structure_type="straddle", reference_tenor="3M", base_qty=1,
                           state="closed", execution_mode="live")
        db.add(s)
        await db.flush()
        db.add(BookedPosition(structure_id=s.id, state="closed", opened_at=closed_at - timedelta(days=3),
                              entry_premium_usd=1000.0, entry_total_cost_usd=1010.0,
                              closed_at=closed_at, net_pnl_usd=None if pnl is None else Decimal(str(pnl)),
                              close_reason=reason))
        await db.commit()


def _d(day: int, hour: int) -> datetime:
    return datetime(2026, 11, day, hour, 0, tzinfo=UTC)


async def test_refresh_folds_one_row_per_day_with_open_close_mtm_and_realized(sqlite_sessionmaker: Any) -> None:
    from sqlalchemy import select

    from persistence.models import PortfolioDaily
    from persistence.rollups import read_nav_series, read_portfolio_daily, refresh_portfolio_daily

    await _seed_account(sqlite_sessionmaker, [
        (_d(4, 8), 100_000), (_d(4, 20), 100_500),          # day 4: open 100000 close 100500
        (_d(5, 9), 100_400), (_d(5, 15), 101_200), (_d(5, 23), 101_000),  # day 5 close 101000
        (_d(6, 8), 101_300),                                # day 6 (today) close 101300
    ])
    await _seed_close(sqlite_sessionmaker, closed_at=_d(5, 14), pnl=250.0)
    await _seed_close(sqlite_sessionmaker, closed_at=_d(5, 16), pnl=None, reason="reconciled_flat_at_ib")

    async with sqlite_sessionmaker() as db:
        out = await refresh_portfolio_daily(db, now=NOW)
        rows = await read_portfolio_daily(db, days=10, now=NOW)
    assert out["rows"] == 3 and len(rows) == 3
    d4, d5, d6 = rows
    assert (d4.day, float(d4.net_liq_open), float(d4.net_liq_close), d4.n_samples) == (date(2026, 11, 4), 100_000.0, 100_500.0, 2)
    assert d4.mtm_usd is None, "no previous close: the first day has no MTM"
    assert float(d5.mtm_usd) == 500.0 and float(d5.realized_usd) == 250.0 and d5.n_closed == 1
    assert float(d6.mtm_usd) == 300.0 and d6.n_closed == 0
    async with sqlite_sessionmaker() as db:
        nav, computed_at = await read_nav_series(db, days=10, now=NOW)
        n_rows = len((await db.execute(select(PortfolioDaily))).scalars().all())
    assert [v for _, v in nav] == [100_500.0, 101_000.0, 101_300.0] and computed_at is not None
    assert n_rows == 3


async def test_rolling_refresh_upserts_today_and_keeps_the_mtm_chain(sqlite_sessionmaker: Any) -> None:
    from persistence.rollups import ROLLING_DAYS, read_portfolio_daily, refresh_portfolio_daily

    await _seed_account(sqlite_sessionmaker, [(_d(1, 20), 90_000), (_d(2, 20), 91_000), (_d(3, 20), 92_000),
                                              (_d(4, 20), 93_000), (_d(5, 20), 94_000), (_d(6, 8), 94_500)])
    async with sqlite_sessionmaker() as db:
        await refresh_portfolio_daily(db, days=10, now=NOW)
    # A later sample moves today's close; the rolling refresh (3 days) must
    # recompute today against the stored close of the day before its window.
    await _seed_account(sqlite_sessionmaker, [(_d(6, 11), 95_200)])
    async with sqlite_sessionmaker() as db:
        out = await refresh_portfolio_daily(db, now=NOW)
        rows = await read_portfolio_daily(db, days=10, now=NOW)
    assert out["days"] == ROLLING_DAYS and len(rows) == 6, "rolling refresh rewrites, never duplicates"
    by_day = {r.day: r for r in rows}
    assert float(by_day[date(2026, 11, 6)].net_liq_close) == 95_200.0
    assert float(by_day[date(2026, 11, 6)].mtm_usd) == 1_200.0
    assert float(by_day[date(2026, 11, 4)].mtm_usd) == 1_000.0, "day 4 is the first of the window: chained on day 3's stored close"
    assert by_day[date(2026, 11, 1)].mtm_usd is None


async def test_refresh_on_an_empty_history_writes_nothing(sqlite_sessionmaker: Any) -> None:
    from persistence.rollups import read_portfolio_daily, refresh_portfolio_daily

    async with sqlite_sessionmaker() as db:
        out = await refresh_portfolio_daily(db, now=NOW)
        assert out["rows"] == 0 and await read_portfolio_daily(db, days=10) == []


async def test_attribution_row_round_trips_and_ages_out(sqlite_sessionmaker: Any) -> None:
    from persistence.rollups import read_attribution_latest, write_attribution_latest

    result = {
        "per_position": [{"id": 1, "source": "ib_live", "attrib_tenor": "3M", "actual_pnl_usd": 12.5,
                          "delta_pnl_usd": 10.0, "gamma_pnl_usd": 1.0, "vega_pnl_usd": 1.0,
                          "theta_pnl_usd": 0.5, "residual_usd": 0.0}],
        "meta": {"lookback_hours": 24, "computed_at": NOW.isoformat(),
                 "window_start": (NOW - timedelta(hours=24)).isoformat(), "forward_source": "future_mark"},
    }
    async with sqlite_sessionmaker() as db:
        await write_attribution_latest(db, result)
        again = dict(result, meta={**result["meta"], "forward_source": "spot_cache"})
        await write_attribution_latest(db, again)  # upsert on lookback_hours
        fresh = await read_attribution_latest(db, lookback_hours=24, now=NOW + timedelta(minutes=5))
        stale = await read_attribution_latest(db, lookback_hours=24, now=NOW + timedelta(minutes=20))
        other = await read_attribution_latest(db, lookback_hours=4, now=NOW)
    assert fresh is not None and fresh["per_position"] == result["per_position"]
    assert fresh["meta"]["forward_source"] == "spot_cache" and fresh["meta"]["lookback_hours"] == 24
    assert stale is None and other is None


def _row(**kw: Any) -> SimpleNamespace:
    base = dict(id=1, structure="EUUZ6 C1130", product_label="Straddle", side="BUY", tenor="3M",
                expiry=date(2026, 12, 4), trade_id=7, contract_id=99, nominal_eur=1_250_000, structure_type="straddle",
                pnl_now=300.0, spot_now=0.0131, iv_now=0.0615, delta_now=600_000.0, gamma_now=14.7, vega_now=2779.58,
                theta_now=-92.0, pnl_then=0.0, spot_then=0.0123, iv_then=0.0605, delta_then=600_000.0,
                gamma_then=14.7, vega_then=2779.58, theta_then=-92.0, t_then=NOW - timedelta(hours=24))
    base.update(kw)
    return SimpleNamespace(**base)


def _fake_db(ib_rows: list[Any], booked_rows: list[Any], fwd: tuple | None, fwd_then: tuple | None) -> AsyncMock:
    results = []
    for payload in (ib_rows, booked_rows):
        r = MagicMock()
        r.all.return_value = payload
        results.append(r)
    r1 = MagicMock()
    r1.first.return_value = fwd
    results.append(r1)
    if fwd is not None:
        r2 = MagicMock()
        r2.first.return_value = fwd_then
        results.append(r2)
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=results)
    return db


async def test_compute_attribution_uses_the_future_forward_and_tags_the_source() -> None:
    from persistence.rollups import compute_attribution

    out = await compute_attribution(_fake_db([_row()], [], (1.1241,), (1.1198,)), now=NOW, lookback_hours=24)
    row = out["per_position"][0]
    assert out["meta"]["forward_source"] == "future_mark" and out["meta"]["lookback_hours"] == 24
    assert row["source"] == "ib_live" and row["attrib_tenor"] == "1M" and row["wing"] in ("Put wing", "Call wing", "Body (ATM)")
    # anchored on the t-1 greek: delta_usd / F_then * dF with dF = +0.0043
    assert row["delta_pnl_usd"] == pytest.approx(600_000.0 / 1.1198 * 0.0043, rel=1e-3)
    assert row["vega_pnl_usd"] == pytest.approx(2779.58 * 0.1, rel=1e-3), "IV fraction -> vol points"
    assert row["actual_pnl_usd"] == 300.0


async def test_compute_attribution_falls_back_to_the_spot_cache_when_no_future_is_open() -> None:
    from persistence.rollups import compute_attribution

    db = _fake_db([_row()], [], None, None)
    out = await compute_attribution(db, now=NOW, lookback_hours=24, spot_fallback=(1.1250, 1.1200))
    assert out["meta"]["forward_source"] == "spot_cache"
    assert out["per_position"][0]["delta_pnl_usd"] == pytest.approx(600_000.0 / 1.1200 * 0.005, rel=1e-3)
    assert db.execute.await_count == 3, "no history walk when no 6E is open"

    none = await compute_attribution(_fake_db([_row()], [], None, None), now=NOW, lookback_hours=24)
    assert none["meta"]["forward_source"] == "none" and none["per_position"][0]["delta_pnl_usd"] is None


async def test_assemble_attribution_pivots_the_stored_rows_without_recomputing() -> None:
    from core.risk.attribution import assemble_attribution

    rows = [
        {"source": "ib_live", "attrib_tenor": "3M", "actual_pnl_usd": 100.0, "delta_pnl_usd": 60.0,
         "gamma_pnl_usd": 10.0, "vega_pnl_usd": 20.0, "theta_pnl_usd": 5.0, "residual_usd": 5.0},
        {"source": "booked", "attrib_tenor": "3M", "actual_pnl_usd": 999.0, "delta_pnl_usd": 999.0,
         "gamma_pnl_usd": 0.0, "vega_pnl_usd": 0.0, "theta_pnl_usd": 0.0, "residual_usd": 0.0},
    ]
    meta = {"lookback_hours": 24, "computed_at": NOW.isoformat(), "window_start": "x", "forward_source": "future_mark"}
    flat = assemble_attribution(rows, meta, source="ib_live", group_by=None)
    assert flat["totals"]["actual_pnl_usd"] == 100.0 and len(flat["per_position"]) == 1 and flat["source"] == "ib_live"
    pivot = assemble_attribution(rows, meta, source="all", group_by="tenor")
    assert pivot["group_by"] == "tenor" and pivot["groups"][0]["label"] == "3M"
    assert pivot["totals"]["actual_pnl_usd"] == 1099.0 and pivot["computed_at"] == NOW.isoformat()

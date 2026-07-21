"""Unit tests for the R11 G-portfolio additions to portfolio_panel.

  * `_sharpe_and_drawdown` — pure stats compute (no DB).
  * `cash_holdings` — ORM-only endpoint, exercised on in-memory SQLite.

`daily-pnl` / `stats` SQL bodies use Postgres-only constructs (date_trunc,
make_interval, FILTER, DISTINCT ON) → covered by the db_integration job, not here.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

pytest.importorskip("pytest_asyncio")
pytestmark = pytest.mark.asyncio


def _coerce_bigint_to_integer(metadata) -> None:
    from sqlalchemy import BigInteger, Integer
    for table in metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, BigInteger):
                col.type = Integer()


async def _make_session():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from persistence.models import Base

    _coerce_bigint_to_integer(Base.metadata)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False), engine


# ───────────────────────────── _sharpe_and_drawdown ─────────────────────────


# pytestmark applies the asyncio mark module-wide; these pure tests are async
# (no await) only to satisfy that mark without a per-test override.
async def test_sharpe_drawdown_short_series_is_none():
    from api.routers.portfolio_panel import _sharpe_and_drawdown
    assert _sharpe_and_drawdown([100.0, 101.0]) == (None, None, None)


async def test_sharpe_drawdown_flat_curve_zero_dd_no_sharpe():
    from api.routers.portfolio_panel import _sharpe_and_drawdown
    sharpe, max_dd, cur_dd = _sharpe_and_drawdown([100.0, 100.0, 100.0])
    assert sharpe is None       # zero variance → undefined
    assert max_dd == 0.0
    assert cur_dd == 0.0


async def test_sharpe_drawdown_detects_peak_to_trough():
    from api.routers.portfolio_panel import _sharpe_and_drawdown
    # peak 120 → trough 90 = −25% max DD; ends at 90 (still in drawdown).
    _, max_dd, cur_dd = _sharpe_and_drawdown([100.0, 120.0, 90.0])
    assert max_dd == pytest.approx(-0.25, abs=1e-9)
    assert cur_dd == pytest.approx(-0.25, abs=1e-9)


async def test_sharpe_positive_for_uptrend():
    from api.routers.portfolio_panel import _sharpe_and_drawdown
    sharpe, max_dd, _ = _sharpe_and_drawdown([100.0, 101.0, 102.0, 103.0, 104.0])
    assert sharpe is not None and sharpe > 0
    assert max_dd == 0.0        # monotone up → never below peak


# ───────────────────────────── _var_stats ───────────────────────────────────


async def test_var_stats_short_series_is_none():
    from api.routers.portfolio_panel import _var_stats
    assert _var_stats([1.0, -2.0, 3.0]) is None


async def test_var_stats_quantiles_and_es():
    from api.routers.portfolio_panel import _var_stats
    # 100 deltas from -100..-1 (losses) → VaR/ES on the left tail.
    deltas = [float(-x) for x in range(1, 101)]  # -1 .. -100
    out = _var_stats(deltas)
    assert out is not None
    # 1% quantile of the sorted (−100..−1) list ≈ −99.01; 5% ≈ −95.05
    assert out["var_99"] == pytest.approx(-99.01, abs=0.5)
    assert out["var_95"] == pytest.approx(-95.05, abs=0.5)
    # ES99 = mean of losses ≤ var99 (the worst ~1) → about −100
    assert out["es_99"] <= out["var_99"]
    assert out["n"] == 100.0


async def test_percentile_interpolates():
    from api.routers.portfolio_panel import _percentile
    assert _percentile([0.0, 10.0], 0.5) == pytest.approx(5.0)
    assert _percentile([], 0.5) is None


async def test_histogram_bins_and_counts():
    from api.routers.portfolio_panel import _histogram
    h = _histogram([0.0, 1.0, 2.0, 3.0, 4.0], nbins=2)
    assert len(h) == 2
    assert sum(b["count"] for b in h) == 5
    assert h[0]["lo"] == 0.0 and h[-1]["hi"] == 4.0
    assert _histogram([1.0], nbins=4) == []        # too few
    assert _histogram([2.0, 2.0, 2.0], nbins=4) == []  # zero range


# ───────────────────────────── cash_holdings ────────────────────────────────


async def test_cash_holdings_empty_table_is_missing():
    from api.routers.portfolio_panel import cash_holdings

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            out = await cash_holdings(db)
        assert out["currencies"] == []
        assert out["total_usd"] == 0
        assert out["freshness"] == "missing"
    finally:
        await engine.dispose()


async def test_cash_holdings_values_eur_in_usd_via_spot():
    from api.routers.portfolio_panel import cash_holdings
    from persistence.models import AccountHistory, VolSurface

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            db.add(VolSurface(
                timestamp=datetime(2026, 6, 16, 11, tzinfo=UTC),
                underlying="EURUSD", spot=Decimal("1.10"), forward=Decimal("1.10"),
                surface_data={},
            ))
            db.add(AccountHistory(
                timestamp=datetime(2026, 6, 16, 12, tzinfo=UTC),
                net_liq_usd=Decimal("1000"), cash_usd=Decimal("500"),
                currencies={"USD": 500.0, "EUR": 200.0, "JPY": 1000.0},
            ))
            await db.commit()
        async with maker() as db:
            out = await cash_holdings(db)

        assert out["eurusd_spot"] == pytest.approx(1.10)
        by = {r["ccy"]: r for r in out["currencies"]}
        assert by["USD"]["usd_value"] == pytest.approx(500.0)
        assert by["USD"]["rate"] == 1.0
        assert by["EUR"]["usd_value"] == pytest.approx(220.0)   # 200 × 1.10
        assert by["EUR"]["rate"] == pytest.approx(1.10)
        assert by["JPY"]["rate"] is None                        # no rate → unvalued
        assert by["JPY"]["usd_value"] is None
        assert out["total_usd"] == pytest.approx(720.0)         # 500 + 220 (JPY excluded)
        assert out["freshness"] in {"fresh", "stale", "missing"}
        # largest USD value first; unconvertible last
        assert out["currencies"][0]["ccy"] == "USD"
        assert out["currencies"][-1]["ccy"] == "JPY"
    finally:
        await engine.dispose()


async def test_cash_holdings_handles_ib_tag_dict_currencies():
    # Regression: IB stores per-currency TAG DICTS (CashBalance / ExchangeRate /
    # …), not bare scalars — the handler used to crash on float(dict).
    from api.routers.portfolio_panel import cash_holdings
    from persistence.models import AccountHistory, VolSurface

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            db.add(VolSurface(
                timestamp=datetime(2026, 6, 25, 11, tzinfo=UTC),
                underlying="EURUSD", spot=Decimal("1.10"), forward=Decimal("1.10"),
                surface_data={},
            ))
            db.add(AccountHistory(
                timestamp=datetime(2026, 6, 25, 12, tzinfo=UTC),
                net_liq_usd=Decimal("1000"), cash_usd=Decimal("500"),
                currencies={
                    "USD": {"CashBalance": 500.0, "ExchangeRate": 0.88, "UnrealizedPnL": -5.0},
                    "EUR": {"CashBalance": 200.0, "ExchangeRate": 1.0},
                },
            ))
            await db.commit()
        async with maker() as db:
            out = await cash_holdings(db)  # must NOT raise on the dict shape
        by = {r["ccy"]: r for r in out["currencies"]}
        assert by["USD"]["settled"] == pytest.approx(500.0)        # CashBalance extracted
        assert by["USD"]["usd_value"] == pytest.approx(500.0)      # USD 1:1
        assert by["EUR"]["settled"] == pytest.approx(200.0)
        assert by["EUR"]["usd_value"] == pytest.approx(220.0)      # 200 × 1.10 spot (not ExchangeRate)
    finally:
        await engine.dispose()

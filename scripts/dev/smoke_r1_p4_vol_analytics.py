"""Functional smoke test of the R1 PR #4 vol and analytics ORM models.

Runs end-to-end scenarios against a real Postgres and prints observed behavior.
Covers the three tables added in PR #4:
    1. Insert a VolSurface with nested JSONB payload, roundtrip via ORM + raw SQL
    2. Verify Postgres JSONB operators (->, #>>) work on surface_data
    3. Insert two VolSurfaces with same (timestamp, underlying) -> UNIQUE violation
    4. Insert a Signal, verify signal_type CHECK rejects 'FLAT'
    5. Insert a BacktestRun with equity_curve as JSONB array, verify roundtrip
    6. Verify BacktestRun.created_at is populated by server_default

Usage (PowerShell):
    docker compose -f docker-compose.dev.yml up -d postgres
    $env:DATABASE_URL = "postgresql+asyncpg://fxvol:fxvol@localhost:5433/fxvol"
    python scripts/dev/db_create_tables.py
    python scripts/dev/smoke_r1_p4_vol_analytics.py
    python scripts/dev/db_drop_tables.py

Expected exit code : 0 on success, non-zero if any assertion fails.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from persistence.db import get_session  # noqa: E402
from persistence.models import BacktestRun, Signal, VolSurface  # noqa: E402


def _banner(title: str) -> None:
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


async def step_1_vol_surface_jsonb_roundtrip() -> None:
    _banner("STEP 1 : VolSurface insert + JSONB roundtrip via ORM")
    surface = {
        "1M": {"10dp": 8.33, "25dp": 7.30, "atm": 6.76, "25dc": 6.55, "10dc": 6.80},
        "2M": {"10dp": 8.10, "25dp": 7.20, "atm": 6.80, "25dc": 6.60, "10dc": 6.85},
    }
    async with get_session() as s:
        s.add(
            VolSurface(
                timestamp=datetime(2026, 4, 17, 15, 0, tzinfo=UTC),
                underlying="EUR.USD",
                spot=Decimal("1.08500000"),
                forward=Decimal("1.08700000"),
                surface_data=surface,
                fair_vol_data={"1M": 7.55, "2M": 7.69},
                scan_duration_s=Decimal("2.45"),
            )
        )
    async with get_session() as s:
        vs = (await s.execute(select(VolSurface))).scalar_one()
        assert vs.surface_data == surface
        assert vs.surface_data["1M"]["atm"] == 6.76
        print(f"  OK : roundtrip surface 1M atm = {vs.surface_data['1M']['atm']}")


async def step_2_vol_surface_native_jsonb_operator() -> None:
    _banner("STEP 2 : native Postgres JSONB operator on surface_data")
    async with get_session() as s:
        # Postgres #>> operator traverses a JSONB path and returns text.
        # Fails if column is TEXT, works only if JSONB.
        r = await s.execute(
            text(
                "SELECT surface_data #>> '{1M,atm}' AS atm "
                "FROM vol_surfaces ORDER BY id DESC LIMIT 1"
            )
        )
        atm = r.scalar_one()
        print(f"  OK : Postgres surface_data #>> '{{1M,atm}}' returned {atm!r}")
        assert atm == "6.76"


async def step_3_vol_surface_unique_ts_underlying() -> None:
    _banner("STEP 3 : UNIQUE (timestamp, underlying) rejects duplicates")
    ts = datetime(2026, 4, 17, 16, 0, tzinfo=UTC)
    async with get_session() as s:
        s.add(
            VolSurface(
                timestamp=ts,
                underlying="USD.JPY",
                spot=Decimal("150.00000000"),
                surface_data={"1M": {"atm": 8.50}},
            )
        )
    raised = False
    try:
        async with get_session() as s:
            s.add(
                VolSurface(
                    timestamp=ts,
                    underlying="USD.JPY",
                    spot=Decimal("150.10000000"),
                    surface_data={"1M": {"atm": 8.60}},
                )
            )
    except IntegrityError as exc:
        raised = True
        print(f"  OK : duplicate rejected ({type(exc.orig).__name__})")
    assert raised, "duplicate (ts, underlying) should raise IntegrityError"


async def step_4_signal_check_constraint() -> None:
    _banner("STEP 4 : Signal signal_type CHECK rejects 'FLAT'")
    async with get_session() as s:
        s.add(
            Signal(
                timestamp=datetime(2026, 4, 17, 17, 0, tzinfo=UTC),
                underlying="EUR.USD",
                tenor="1M",
                dte=30,
                sigma_mid=Decimal("6.76000"),
                sigma_fair=Decimal("7.55000"),
                ecart=Decimal("0.79000"),
                signal_type="CHEAP",
            )
        )
    print("  valid signal_type='CHEAP' inserted OK")
    raised = False
    try:
        async with get_session() as s:
            s.add(
                Signal(
                    timestamp=datetime(2026, 4, 17, 17, 1, tzinfo=UTC),
                    underlying="EUR.USD",
                    tenor="2M",
                    dte=60,
                    sigma_mid=Decimal("6.80000"),
                    sigma_fair=Decimal("6.80000"),
                    ecart=Decimal("0.00000"),
                    signal_type="FLAT",
                )
            )
    except IntegrityError as exc:
        raised = True
        print(f"  OK : signal_type='FLAT' rejected ({type(exc.orig).__name__})")
    assert raised, "invalid signal_type should violate CHECK constraint"


async def step_5_backtest_run_jsonb_arrays() -> None:
    _banner("STEP 5 : BacktestRun JSONB arrays (equity_curve + trades_log)")
    equity_curve = [
        {"date": "2025-01-01", "equity": 100000.0},
        {"date": "2025-06-30", "equity": 108500.5},
        {"date": "2025-12-31", "equity": 115200.0},
    ]
    trades_log = [
        {"ts": "2025-02-15", "side": "BUY", "pnl": 520.0},
        {"ts": "2025-09-10", "side": "SELL", "pnl": -120.5},
    ]
    async with get_session() as s:
        s.add(
            BacktestRun(
                strategy_name="vol_rv_spread_v1",
                parameters={"threshold_bps": 20, "tenor": "1M"},
                start_date=date(2025, 1, 1),
                end_date=date(2025, 12, 31),
                sharpe_ratio=Decimal("1.4523"),
                max_drawdown_pct=Decimal("5.8100"),
                hit_rate=Decimal("0.5833"),
                total_return_pct=Decimal("15.2000"),
                n_trades=12,
                equity_curve=equity_curve,
                trades_log=trades_log,
            )
        )
    async with get_session() as s:
        run = (await s.execute(select(BacktestRun))).scalar_one()
        assert run.equity_curve == equity_curve
        assert run.trades_log == trades_log
        assert run.sharpe_ratio == Decimal("1.4523")
        print(f"  OK : strategy={run.strategy_name}, sharpe={run.sharpe_ratio}, n_trades={run.n_trades}")
        print(f"       equity_curve has {len(run.equity_curve)} points")


async def step_6_backtest_run_created_at_server_default() -> None:
    _banner("STEP 6 : BacktestRun.created_at populated by server_default")
    async with get_session() as s:
        run = (await s.execute(select(BacktestRun))).scalar_one()
        assert run.created_at is not None
        print(f"  OK : created_at = {run.created_at} (server NOW())")


async def main() -> None:
    await step_1_vol_surface_jsonb_roundtrip()
    await step_2_vol_surface_native_jsonb_operator()
    await step_3_vol_surface_unique_ts_underlying()
    await step_4_signal_check_constraint()
    await step_5_backtest_run_jsonb_arrays()
    await step_6_backtest_run_created_at_server_default()
    print()
    print("=" * 70)
    print("  ALL 6 FUNCTIONAL CHECKS PASSED")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())

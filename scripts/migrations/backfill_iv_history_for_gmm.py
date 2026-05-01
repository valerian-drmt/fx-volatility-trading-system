"""Backfill 1 year of daily IV (≈30d ATM) into ``feature_history``.

Goal : seed the table so the GMM regime classifier (Step 1 §3 zone 2) has
≥ 50 observations and can fit immediately, instead of waiting weeks of
live cycles.

Limitation
----------
IB does not expose historical EOD bars on individual FOP contracts to
paper / non-bundle accounts (Error 162 "No data of type EODChart" on
both ``OPTION_IMPLIED_VOLATILITY`` and ``TRADES`` whatToShow). Therefore
**we cannot reconstruct per-tenor IV** (1M / 3M / 6M separately) from
historical, only the aggregated whole-chain ATM IV via this script.

Consequence for ``feature_history`` :
  - ``iv_atm_3m_pct`` filled (we use the ~30d ATM index as a proxy)
  - ``iv_atm_1m_pct``, ``iv_atm_6m_pct``, ``term_slope_pct`` stay NULL
    on historical rows
  - The live cycle (vol-engine, 180s) computes all 3 tenors from the
    real chain and fills these columns going forward — ~480 rows/day
    so ``term_slope`` has full coverage after ~1 day of uptime.

What it does
------------
1. Connect to IB Gateway with a unique clientId (99) to avoid clashing
   with the running vol-engine (clientId=2).
2. Fetch 1 year of daily ``OPTION_IMPLIED_VOLATILITY`` bars on the EUR
   continuous future. IB returns one IV per day (~30d ATM equivalent
   computed across the full chain by IB itself).
3. Compute rolling 30d std of IV → ``vol_of_vol_30d_pct``.
4. INSERT 252 rows into ``feature_history`` with
   ``source = 'ib_historical_backfill'``. ``term_slope_pct`` stays NULL
   (single-tenor index, no per-tenor breakdown — the live cycle fills
   this column going forward).
5. ON CONFLICT skip on ``(symbol, timestamp)`` so re-running is safe.

Run from inside the ``fxvol-vol-engine`` container :

    docker exec -it fxvol-vol-engine python scripts/migrations/backfill_iv_history_for_gmm.py

Expected runtime : ~10s (1 IB call + 1 batch INSERT). Re-run is idempotent.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# Make ``persistence``, ``shared`` etc. importable when run from /app.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pandas as pd
from ib_insync import IB, Contract
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from persistence.db import get_sessionmaker
from persistence.models import FeatureHistory

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backfill_iv_for_gmm")

IB_HOST = os.environ.get("IB_HOST", "ib-gateway")
IB_PORT = int(os.environ.get("IB_PORT", "4002"))
IB_CLIENT_ID = 99   # distinct from vol-engine (2), execution (5), etc.

SYMBOL = "EURUSD"
DURATION = "1 Y"
ROLLING_VOV_DAYS = 30


async def fetch_iv_series(ib: IB) -> pd.DataFrame:
    """Pull 1 year of daily IV bars on the EUR continuous future."""
    cont = Contract(symbol="EUR", secType="CONTFUT", exchange="CME", currency="USD")
    log.info("Requesting OPTION_IMPLIED_VOLATILITY %s on EUR/CONTFUT...", DURATION)
    bars = await ib.reqHistoricalDataAsync(
        cont,
        endDateTime="",
        durationStr=DURATION,
        barSizeSetting="1 day",
        whatToShow="OPTION_IMPLIED_VOLATILITY",
        useRTH=True,
        formatDate=1,
    )
    if not bars:
        raise RuntimeError("IB returned 0 bars (subscription / contract issue)")
    df = pd.DataFrame([
        {"date": b.date, "iv": float(b.close)} for b in bars if b.close is not None
    ]).sort_values("date").reset_index(drop=True)
    log.info("Got %d daily IV bars (range %s → %s)", len(df), df["date"].min(), df["date"].max())
    return df


def build_feature_rows(df: pd.DataFrame, symbol: str) -> list[dict]:
    """IV (decimal) → percent + rolling std of percent for vol_of_vol."""
    df = df.copy()
    df["iv_pct"] = df["iv"] * 100.0
    df["vov_30d"] = df["iv_pct"].rolling(window=ROLLING_VOV_DAYS, min_periods=20).std()

    rows: list[dict] = []
    for _, r in df.iterrows():
        d = r["date"]
        # Pin to noon UTC — release-time-agnostic placeholder.
        ts = datetime(d.year, d.month, d.day, 12, 0, tzinfo=UTC)
        rows.append({
            "symbol": symbol, "timestamp": ts,
            "iv_atm_1m_pct": None,
            "iv_atm_3m_pct": float(r["iv_pct"]),  # ~30d ATM ≈ 1M tenor proxy
            "iv_atm_6m_pct": None,
            "rv_yz_pct": None,
            "vol_of_vol_30d_pct": float(r["vov_30d"]) if pd.notna(r["vov_30d"]) else None,
            "term_slope_pct": None,
            "vol_level_z90": None, "vol_of_vol_z90": None, "term_slope_z90": None,
        })
    return rows


async def upsert_rows(rows: list[dict]) -> tuple[int, int]:
    """ON CONFLICT (symbol, timestamp) DO NOTHING — idempotent re-runs."""
    if not rows:
        return 0, 0
    inserted = 0
    skipped = 0
    async with get_sessionmaker()() as session:
        # Pre-count existing rows for this symbol so we can report skipped.
        existing_n = (await session.execute(
            select(FeatureHistory.id).where(FeatureHistory.symbol == rows[0]["symbol"])
        )).scalars().all()
        n_before = len(existing_n)

        stmt = pg_insert(FeatureHistory).values(rows).on_conflict_do_nothing(
            index_elements=["symbol", "timestamp"],
        )
        result = await session.execute(stmt)
        await session.commit()
        inserted = result.rowcount or 0
        skipped = len(rows) - inserted
        n_after = (await session.execute(
            select(FeatureHistory.id).where(FeatureHistory.symbol == rows[0]["symbol"])
        )).scalars().all()
        log.info(
            "feature_history before=%d after=%d (delta=%d, skipped=%d)",
            n_before, len(n_after), len(n_after) - n_before, skipped,
        )
    return inserted, skipped


async def main() -> None:
    ib = IB()
    log.info("Connecting to IB %s:%d clientId=%d ...", IB_HOST, IB_PORT, IB_CLIENT_ID)
    await ib.connectAsync(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID, timeout=15)
    try:
        df = await fetch_iv_series(ib)
        rows = build_feature_rows(df, symbol=SYMBOL)
        non_null_iv = sum(1 for r in rows if r["iv_atm_3m_pct"] is not None)
        non_null_vov = sum(1 for r in rows if r["vol_of_vol_30d_pct"] is not None)
        log.info(
            "Built %d rows (iv non-null=%d, vov non-null=%d)",
            len(rows), non_null_iv, non_null_vov,
        )
        inserted, skipped = await upsert_rows(rows)
        log.info("DONE — inserted=%d, skipped_already_present=%d", inserted, skipped)
    finally:
        ib.disconnect()


if __name__ == "__main__":
    asyncio.run(main())

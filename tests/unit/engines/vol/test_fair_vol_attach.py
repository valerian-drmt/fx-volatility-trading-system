"""VolEngine._attach_fair_vol — OHLC → RV → HAR/GARCH → σ_fair^Q on the surface.

Exercises the engine wiring with a synthetic OHLC frame (no IB), proving the
fair-vol fields land on the published surface dict.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("pytest_asyncio")
pytestmark = pytest.mark.asyncio


def _ohlc(n: int = 300, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = 1.10 * np.exp(np.cumsum(rng.normal(0, 0.005, n)))
    o = closes * 1.0002
    return pd.DataFrame({
        "open": o,
        "high": np.maximum(o, closes) * 1.0003,
        "low": np.minimum(o, closes) * 0.9997,
        "close": closes,
    })


def _engine(fetch_ohlc):
    from engines.vol.engine import VolEngine

    return VolEngine(
        ib=None, redis=None, symbol="EURUSD",
        ib_host="h", ib_port=1, client_id=1,
        fetch_fop_chain=None, fetch_ohlc=fetch_ohlc,
    )


async def test_attach_fair_vol_populates_fair_q():
    df = _ohlc()
    eng = _engine(lambda: df)
    out = {"1M": {"atm": {"iv": 0.065}, "dte": 30}, "6M": {"atm": {"iv": 0.085}, "dte": 180}}
    await eng._attach_fair_vol(out)

    assert out.get("_rv_full_pct", 0) > 0
    assert "_fair_q" in out
    fq = out["_fair_q"]["1M"]
    assert fq["sigma_fair_q_pct"] == pytest.approx(fq["sigma_fair_p_pct"] + fq["vrp_vol_pts"])
    assert fq["regime"] in {"calm", "stressed", "pre_event"}


async def test_attach_fair_vol_noop_on_no_history():
    eng = _engine(lambda: None)  # fetcher returns nothing (IB down / market closed)
    out = {"1M": {"atm": {"iv": 0.065}}}
    await eng._attach_fair_vol(out)
    assert "_fair_q" not in out
    assert "_rv_full_pct" not in out


async def test_attach_fair_vol_awaits_async_fetcher():
    df = _ohlc()

    async def _afetch():
        return df

    eng = _engine(_afetch)
    out = {"1M": {"atm": {"iv": 0.065}, "dte": 30}}
    await eng._attach_fair_vol(out)
    assert out.get("_rv_full_pct", 0) > 0

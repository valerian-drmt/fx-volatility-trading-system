import numpy as np
import pandas as pd
import pytest

from services.vol_engine import (
    bs_delta,
    compute_mid_iv,
    filter_liquid,
    pillars_to_scanner_rows,
    reconstruct_pillars,
    strike_to_delta,
)


@pytest.mark.unit
def test_bs_delta_atm_call_near_half():
    # ATM call delta should be close to 0.5
    d = bs_delta(s=1.10, k=1.10, t=0.25, sigma=0.08, right="C")
    assert 0.45 < d < 0.55


@pytest.mark.unit
def test_bs_delta_atm_put_near_minus_half():
    d = bs_delta(s=1.10, k=1.10, t=0.25, sigma=0.08, right="P")
    assert -0.55 < d < -0.45


@pytest.mark.unit
def test_bs_delta_returns_nan_for_zero_vol():
    assert np.isnan(bs_delta(s=1.10, k=1.10, t=0.25, sigma=0.0, right="C"))


@pytest.mark.unit
def test_strike_to_delta_returns_call_delta():
    d = strike_to_delta(s=1.10, k=1.10, t=0.25, sigma=0.08)
    assert 0.45 < d < 0.55


@pytest.mark.unit
def test_filter_liquid_keeps_valid_rows():
    df = pd.DataFrame([
        {"iv_raw": 0.08, "bid": 0.0038, "ask": 0.004, "volume": 10},  # 5% spread, ok
        {"iv_raw": np.nan, "bid": 0.0038, "ask": 0.004, "volume": 10},  # no IV
        {"iv_raw": 0.08, "bid": 0.001, "ask": 0.010, "volume": 10},  # wide spread
        {"iv_raw": 0.08, "bid": 0.0038, "ask": 0.004, "volume": 2},   # low volume
    ])
    result = filter_liquid(df)
    assert result["liquid"].sum() == 1
    assert result.iloc[0]["liquid"]


@pytest.mark.unit
def test_compute_mid_iv_averages_call_put():
    df = pd.DataFrame([
        {"expiry": "20260620", "tenor": "3M", "T": 0.25, "strike": 1.10,
         "moneyness": 0.0, "right": "C", "iv_raw": 0.080, "delta_ib": 0.50,
         "bid": 0.003, "ask": 0.004, "volume": 10, "liquid": True, "ba_spread_pct": 0.05},
        {"expiry": "20260620", "tenor": "3M", "T": 0.25, "strike": 1.10,
         "moneyness": 0.0, "right": "P", "iv_raw": 0.082, "delta_ib": -0.50,
         "bid": 0.003, "ask": 0.004, "volume": 10, "liquid": True, "ba_spread_pct": 0.05},
    ])
    mid = compute_mid_iv(df)
    assert len(mid) == 1
    assert mid.iloc[0]["iv_mid"] == pytest.approx(0.081, abs=0.001)


@pytest.mark.unit
def test_reconstruct_pillars_produces_rr_bf():
    # Build a synthetic mid IV dataset with 5 strikes
    strikes = [1.04, 1.06, 1.085, 1.11, 1.13]
    ivs = [0.095, 0.085, 0.080, 0.083, 0.092]
    rows = []
    for k, iv in zip(strikes, ivs):
        rows.append({
            "expiry": "20260620", "tenor": "3M", "T": 0.25,
            "strike": k, "moneyness": np.log(k / 1.085),
            "iv_mid": iv, "iv_call": iv, "iv_put": iv,
            "delta_call": 0.5, "delta_put": -0.5,
        })
    df_mid = pd.DataFrame(rows)
    pillars = reconstruct_pillars(df_mid, spot=1.085)

    assert len(pillars) == 1
    row = pillars.iloc[0]
    assert row["tenor"] == "3M"
    assert "RR25" in row
    assert "BF25" in row
    assert "K_atm" in row
    assert row["K_atm"] == pytest.approx(1.085, abs=0.01)


@pytest.mark.unit
def test_pillars_to_scanner_rows_flattens_correctly():
    pillars = pd.DataFrame([{
        "tenor": "3M", "spot": 1.085,
        "iv_atm": 0.080, "iv_25dp": 0.085, "iv_25dc": 0.083,
        "iv_10dp": 0.095, "iv_10dc": 0.092,
        "K_atm": 1.085, "K_25dp": 1.06, "K_25dc": 1.11,
        "K_10dp": 1.04, "K_10dc": 1.13,
        "d_atm": 0.50, "d_25dp": 0.25, "d_25dc": 0.75,
        "d_10dp": 0.10, "d_10dc": 0.90,
    }])
    rows = pillars_to_scanner_rows(pillars, spot=1.085)
    assert len(rows) == 5
    labels = [r["delta_label"] for r in rows]
    assert "ATM" in labels
    assert "25Δp" in labels
    assert "10Δc" in labels
    assert all("iv_market_pct" in r for r in rows)
